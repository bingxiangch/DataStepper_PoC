"""Deterministic natural-language breakpoints for execution pauses.

This is deliberately a small, fixed translator, not a general breakpoint
language. It recognizes two before-execution pauses: DELETE/DROP tools, and a
filter operation whose predicate excludes cancelled orders. Execution-time
matching uses a fixed tool map and a fixed predicate check; no model is
consulted when deciding whether an operation is gated.
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
_AFTER_PATTERN = re.compile(r"\bafter\b", re.IGNORECASE)
_MAX_DESCRIPTION = 500

_CONDITION_LABEL = "Pause if the agent excludes cancelled orders from the analysis."


class BreakpointDraft(BaseModel):
    natural_language: str
    label: str
    operation_types: list[SupportedOperation] = Field(default_factory=list)
    kind: Literal["operation", "filter_excludes"] = "operation"
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
    The resulting rule can only say "pause before DELETE and/or DROP" or "pause
    before a filter that excludes cancelled orders"; it cannot carry a table
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
    if cancellation_exclusion and operation_types:
        return BreakpointInterpretation(
            supported=False,
            message=(
                "Describe one breakpoint per rule: either a DELETE/DROP pause or a "
                "cancelled-order exclusion, not both in one description."
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
                "Only DELETE/DROP pauses and filters that exclude cancelled orders are "
                "supported in this POC. Mention one of those conditions."
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
    """Return the first active before-execution rule matching the operation.

    This is the only execution-time breakpoint decision. It is a fixed lookup
    over operation types and filter predicates, not a model call.
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
        elif operation_type is not None and operation_type in rule.operation_types:
            return rule
    return None
