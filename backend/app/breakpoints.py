"""Deterministic natural-language breakpoints for execution pauses.

This is deliberately a small, fixed translator, not a general breakpoint
language. It recognizes three before-execution pauses: DELETE/DROP tools, a
filter operation whose predicate excludes cancelled orders, and a join preview
whose payment records are repeated beyond a fixed threshold. Execution-time
matching uses a fixed tool map, a fixed predicate check, and one measured join
effect; no model is consulted when deciding whether an operation is gated.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .models import BreakpointCondition, BreakpointRule, Operation, Run

SupportedOperation = Literal["delete", "drop"]

OPERATION_ORDER: tuple[SupportedOperation, ...] = ("delete", "drop")
OPERATION_LABELS = {"delete": "DELETE", "drop": "DROP"}

# Fixed mapping used by the controller. Only real tools are matched today.
# A future sandbox DROP tool can be added here without changing the parser.
TOOL_OPERATIONS: dict[str, SupportedOperation] = {
    "sandbox_delete_rows": "delete",
    "sandbox_delete_inactive_customers": "delete",
    "sandbox_drop_table": "drop",
}

_OPERATION_PATTERNS: dict[SupportedOperation, re.Pattern[str]] = {
    "delete": re.compile(
        r"\b(?:delete|deletes|deleted|deleting|deletion|"
        r"remove|removes|removed|removing|removal|"
        r"erase|erases|erased|erasing)\b",
        re.IGNORECASE,
    ),
    "drop": re.compile(r"\bdrop(?:s|ped|ping)?\b", re.IGNORECASE),
}
_CANCEL_PATTERN = re.compile(r"\bcancel(?:l)?ed\b", re.IGNORECASE)
_EXCLUDE_PATTERN = re.compile(
    r"\b(?:exclud(?:e|es|ed|ing)|filter(?:s|ed|ing)?\s+out)\b",
    re.IGNORECASE,
)
_ORDER_OR_ROW_PATTERN = re.compile(r"\b(?:orders?|rows?)\b", re.IGNORECASE)
_PAYMENT_PATTERN = re.compile(r"\bpayments?\b", re.IGNORECASE)
_REPETITION_PATTERN = re.compile(
    r"\b(?:repeat(?:s|ed|ing)?|duplicat(?:e|es|ed|ing)|cop(?:y|ies)|fan[- ]?out)\b",
    re.IGNORECASE,
)
_THRESHOLD_PATTERN = re.compile(
    r"\b(?:more than|greater than|above|exceeds?)\s+(\d+)\b",
    re.IGNORECASE,
)
_AFTER_PATTERN = re.compile(r"\bafter\b", re.IGNORECASE)
_MAX_DESCRIPTION = 500
_DATA_EFFECT_METRIC = "max_copies_per_payment_record"

_CONDITION_LABEL = "Pause if the agent excludes cancelled orders from the analysis."


def _payment_repetition_threshold(text: str) -> Optional[int]:
    """Return a fixed threshold when the text describes repeated payments.

    The POC supports one data-effect watchpoint: the maximum number of copies
    of a payment record produced by a join. No arbitrary metric or SQL is
    accepted.
    """
    if not (_PAYMENT_PATTERN.search(text) and _REPETITION_PATTERN.search(text)):
        return None
    match = _THRESHOLD_PATTERN.search(text)
    if match is None:
        return 1
    return max(1, int(match.group(1)))


def _payment_repetition_label(threshold: int) -> str:
    if threshold <= 1:
        return "Pause when a payment record is repeated."
    return f"Pause when a payment record is repeated more than {threshold} times."


class BreakpointDraft(BaseModel):
    natural_language: str
    label: str
    operation_types: list[SupportedOperation] = Field(default_factory=list)
    kind: Literal["operation", "filter_excludes", "data_effect"] = "operation"
    condition: Optional[BreakpointCondition] = None
    phase: Literal["before"] = "before"


class BreakpointInterpretation(BaseModel):
    supported: bool
    message: str
    rule: Optional[BreakpointDraft] = None


def _label(operations: list[SupportedOperation]) -> str:
    names = " or ".join(OPERATION_LABELS[operation] for operation in operations)
    return f"Pause before {names} operations."


def _is_cancelled_exclusion(text: str) -> bool:
    return bool(
        _CANCEL_PATTERN.search(text)
        and _EXCLUDE_PATTERN.search(text)
        and _ORDER_OR_ROW_PATTERN.search(text)
    )


def interpret_breakpoint(text: str) -> BreakpointInterpretation:
    """Translate a small natural-language request into a supported rule.

    Unsupported or ambiguous wording is rejected rather than silently widened.
    The resulting rule can only say "pause before DELETE and/or DROP", "pause
    before a filter that excludes cancelled orders", or "pause when a join
    preview repeats a payment record beyond N copies"; it cannot carry a table
    name, SQL predicate, or arbitrary condition.
    """
    raw = (text or "").strip()
    if not raw:
        return BreakpointInterpretation(
            supported=False,
            message="Describe a breakpoint in natural language.",
        )
    if len(raw) > _MAX_DESCRIPTION:
        return BreakpointInterpretation(
            supported=False,
            message=f"Keep the breakpoint description under {_MAX_DESCRIPTION} characters.",
        )
    if _AFTER_PATTERN.search(raw):
        return BreakpointInterpretation(
            supported=False,
            message="This POC supports only breakpoints before execution, not after an operation.",
        )

    operation_types = [
        operation for operation in OPERATION_ORDER if _OPERATION_PATTERNS[operation].search(raw)
    ]
    cancellation_exclusion = _is_cancelled_exclusion(raw)
    payment_threshold = _payment_repetition_threshold(raw)
    condition_count = sum(
        [bool(operation_types), cancellation_exclusion, payment_threshold is not None]
    )
    if condition_count > 1:
        return BreakpointInterpretation(
            supported=False,
            message=(
                "Describe one breakpoint per rule: a DELETE/DROP pause, a "
                "cancelled-order exclusion, or a payment-repetition watchpoint, "
                "not more than one in a description."
            ),
        )
    if payment_threshold is not None:
        label = _payment_repetition_label(payment_threshold)
        return BreakpointInterpretation(
            supported=True,
            message=f"Interpreted rule: {label}",
            rule=BreakpointDraft(
                natural_language=raw,
                label=label,
                kind="data_effect",
                condition=BreakpointCondition(
                    metric=_DATA_EFFECT_METRIC,
                    threshold=payment_threshold,
                ),
            ),
        )
    if cancellation_exclusion:
        return BreakpointInterpretation(
            supported=True,
            message=f"Interpreted rule: {_CONDITION_LABEL}",
            rule=BreakpointDraft(
                natural_language=raw,
                label=_CONDITION_LABEL,
                kind="filter_excludes",
                condition=BreakpointCondition(),
            ),
        )
    if not operation_types:
        return BreakpointInterpretation(
            supported=False,
            message=(
                "Only DELETE/DROP pauses, filters that exclude cancelled orders, and "
                "repeated payment records in joins are supported in this POC. Mention "
                "one of those conditions."
            ),
        )

    label = _label(operation_types)
    return BreakpointInterpretation(
        supported=True,
        message=f"Interpreted rule: {label}",
        rule=BreakpointDraft(
            natural_language=raw,
            label=label,
            operation_types=operation_types,
        ),
    )


def operation_type_for_tool(tool: str) -> Optional[SupportedOperation]:
    return TOOL_OPERATIONS.get(tool)


def _normalized_value(value: Any) -> str:
    return str(value).strip().lower().replace("cancelled", "canceled")


def _condition_excludes_cancelled(where: Any, column: str, value: str) -> bool:
    """Return True only when the predicate necessarily rejects ``value``.

    Filters combine all conditions with AND, so one excluding condition is
    enough. Substring and null tests are deliberately conservative because they
    do not prove that a cancelled order is excluded.
    """
    if isinstance(where, dict):
        items = [where]
    elif isinstance(where, list):
        items = where
    else:
        return False
    for item in items:
        if not isinstance(item, dict) or item.get("column") != column:
            continue
        op = item.get("op", "eq")
        raw = item.get("value")
        if op == "eq":
            if raw is not None and _normalized_value(raw) != value:
                return True
        elif op == "ne":
            if raw is not None and _normalized_value(raw) == value:
                return True
        elif op == "in":
            if isinstance(raw, list) and all(
                _normalized_value(candidate) != value for candidate in raw
            ):
                return True
        elif op == "not_in":
            if isinstance(raw, list) and any(
                _normalized_value(candidate) == value for candidate in raw
            ):
                return True
    return False


def _filter_excludes_cancelled(
    operation: Operation, condition: BreakpointCondition
) -> bool:
    if operation.tool != "filter_rows":
        return False
    return _condition_excludes_cancelled(
        operation.args.get("where"), condition.column, condition.value
    )


def matching_breakpoint(run: Run, operation: Operation) -> Optional[BreakpointRule]:
    """Return the first active operation or filter rule matching the operation.

    Data-effect watchpoints are matched separately against a controller preview,
    because their condition depends on the measured join result. This function
    never calls a model.
    """
    operation_type = operation_type_for_tool(operation.tool)
    for rule in run.breakpoints:
        if not rule.active or rule.phase != "before":
            continue
        if rule.kind == "filter_excludes":
            if rule.condition is not None and _filter_excludes_cancelled(
                operation, rule.condition
            ):
                return rule
        elif rule.kind == "operation" and operation_type is not None:
            if operation_type in rule.operation_types:
                return rule
    return None


def matching_data_effect_breakpoint(
    run: Run,
    operation: Operation,
    details: dict[str, Any],
) -> Optional[BreakpointRule]:
    """Match a data-effect watchpoint against a read-only join preview.

    Only the fixed ``max_copies_per_payment_record`` effect is supported. The
    preview is computed by the controller; this function only compares a
    measured integer with the rule threshold.
    """
    if operation.tool != "join_tables":
        return None
    for rule in run.breakpoints:
        if not rule.active or rule.phase != "before" or rule.kind != "data_effect":
            continue
        condition = rule.condition
        if condition is None or condition.metric != _DATA_EFFECT_METRIC:
            continue
        value = details.get(condition.metric)
        threshold = condition.threshold if condition.threshold is not None else 1
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > threshold:
            return rule
    return None
