"""Typed state for semantic, execution, and data layers.

These are explicit records produced by the agent/controller.  They are not
hidden chain-of-thought and never contain private model reasoning.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


BindingStatus = Literal["proposed", "confirmed", "revised"]
EvidenceStatus = Literal["OBSERVED", "PREVIEW", "PROPOSED"]
RunStatus = Literal[
    "paused_before_step",
    "paused_before_operation",
    "running",
    "awaiting_approval",
    "completed",
    "failed",
    "cancelled",
]
OpStatus = Literal["pending", "completed", "failed", "awaiting_approval", "rejected", "skipped"]
StepStatus = Literal["pending", "completed", "failed", "skipped"]


class SemanticBinding(BaseModel):
    concept: str
    source_table: str
    column: str
    status: BindingStatus = "proposed"
    note: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class MetricDefinition(BaseModel):
    name: str
    definition: str
    unit: str = "number"
    status: BindingStatus = "proposed"


class TaskSpecVersion(BaseModel):
    id: str
    run_id: str
    version: int
    objective: str
    population: str
    time_scope: str
    metric_definitions: list[MetricDefinition] = Field(default_factory=list)
    semantic_bindings: list[SemanticBinding] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)


class Fact(BaseModel):
    label: str
    value: str


class EvidenceRecord(BaseModel):
    id: str
    operation_id: str
    kind: str
    status: EvidenceStatus
    label: str
    summary: str
    facts: list[Fact] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    source_artifact_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)


class Operation(BaseModel):
    id: str
    step_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: OpStatus = "pending"
    summary: str = ""
    requires_approval: bool = False
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class DependencyNode(BaseModel):
    label: str
    step_id: Optional[str] = None
    status: Literal["PLANNED", "OBSERVED"] = "PLANNED"


class WorkflowStep(BaseModel):
    id: str
    index: int
    title: str
    goal: str
    status: StepStatus = "pending"
    inherited: bool = False
    understanding: str = ""
    target_data: list[str] = Field(default_factory=list)
    rationale: str = ""
    operations: list[Operation] = Field(default_factory=list)
    dependency_chain: list[DependencyNode] = Field(default_factory=list)


class MetricValue(BaseModel):
    name: str
    value: Optional[float] = None
    formatted: str = "—"
    numerator: Optional[float] = None
    denominator: Optional[float] = None
    unit: str = "number"
    definition: str = ""
    source_step_id: Optional[str] = None
    source_artifact_id: Optional[str] = None
    extras: dict[str, Any] = Field(default_factory=dict)


class Report(BaseModel):
    title: str
    summary: str
    metrics: list[MetricValue] = Field(default_factory=list)
    table_name: Optional[str] = None
    table_rows: Optional[int] = None
    notes: list[str] = Field(default_factory=list)
    step_links: list[dict[str, str]] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)


class FeedbackMessage(BaseModel):
    id: str
    step_id: str
    role: Literal["user", "agent", "system"]
    text: str
    created_at: str = Field(default_factory=utcnow)
    proposal_id: Optional[str] = None


class StepUpdate(BaseModel):
    step_id: str
    title: Optional[str] = None
    goal: Optional[str] = None
    understanding: Optional[str] = None
    target_data: Optional[list[str]] = None
    rationale: Optional[str] = None
    operations: Optional[list[dict[str, Any]]] = None


class RevisionChange(BaseModel):
    kind: Literal["binding", "metric", "plan", "assumption"]
    description: str
    before: str = ""
    after: str = ""


class Investigation(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    facts: list[Fact] = Field(default_factory=list)


class RevisionProposal(BaseModel):
    id: str
    run_id: str
    step_id: str
    user_text: str
    explanation: str
    changes: list[RevisionChange] = Field(default_factory=list)
    proposed_bindings: Optional[list[SemanticBinding]] = None
    proposed_metrics: Optional[list[MetricDefinition]] = None
    proposed_assumptions: Optional[list[str]] = None
    step_updates: list[StepUpdate] = Field(default_factory=list)
    investigations: list[Investigation] = Field(default_factory=list)
    earliest_affected_step: int = 0
    execution_changed: bool = False
    status: Literal["pending", "accepted", "rejected", "no_change"] = "pending"
    created_at: str = Field(default_factory=utcnow)


class Checkpoint(BaseModel):
    id: str
    boundary: int
    label: str
    tables: dict[str, str]
    task_spec_version: int
    # Sandbox checkpoints carry an immutable SQLite snapshot, stored relative to
    # the run directory. Replay restores this file before re-executing work.
    sandbox_snapshot: Optional[str] = None
    created_at: str = Field(default_factory=utcnow)


class BreakpointCondition(BaseModel):
    """A fixed exclusion predicate, not an arbitrary SQL expression."""

    column: Literal["order_status"] = "order_status"
    value: Literal["canceled"] = "canceled"


class BreakpointRule(BaseModel):
    """A deterministic, user-configured before-execution pause.

    Two fixed kinds are supported: a pause before DELETE/DROP tools, and a pause
    before a filter operation whose predicate excludes cancelled orders. The
    rule carries no arbitrary SQL or free-form execution condition.
    """

    id: str
    natural_language: str
    label: str
    operation_types: list[Literal["delete", "drop"]] = Field(default_factory=list)
    kind: Literal["operation", "filter_excludes"] = "operation"
    condition: Optional[BreakpointCondition] = None
    phase: Literal["before"] = "before"
    active: bool = True
    created_at: str = Field(default_factory=utcnow)


class ApprovalRequest(BaseModel):
    id: str
    operation_id: str
    step_id: str
    tool: str
    args: dict[str, Any]
    label: str
    facts: list[Fact] = Field(default_factory=list)
    status: Literal["pending", "approved", "rejected"] = "pending"
    reason: Optional[str] = None
    # A snapshot of the rule that matched when the gate opened. The snapshot is
    # kept even if the rule is later removed, so the recorded pause is explicit.
    matched_breakpoint: Optional[BreakpointRule] = None
    # True when the operation is gated by the fixed mandatory policy, not only
    # by a user-configured breakpoint.
    mandatory: bool = True
    created_at: str = Field(default_factory=utcnow)


class Run(BaseModel):
    id: str
    parent_run_id: Optional[str] = None
    replay_of_revision_id: Optional[str] = None
    task: str
    mode: Literal["scripted", "live"]
    dataset: str
    scenario_id: str
    reference_kind: Optional[str] = None
    planner_label: str
    status: RunStatus = "paused_before_step"
    cursor_step: int = 0
    cursor_op: int = 0
    inside: bool = False
    steps: list[WorkflowStep] = Field(default_factory=list)
    tables: dict[str, str] = Field(default_factory=dict)
    task_specs: list[TaskSpecVersion] = Field(default_factory=list)
    current_spec_version: int = 1
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    messages: list[FeedbackMessage] = Field(default_factory=list)
    revisions: list[RevisionProposal] = Field(default_factory=list)
    accepted_revision_id: Optional[str] = None
    child_run_ids: list[str] = Field(default_factory=list)
    report: Optional[Report] = None
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    reference_check: Optional[dict[str, Any]] = None
    approval: Optional[ApprovalRequest] = None
    approvals: list[ApprovalRequest] = Field(default_factory=list)
    breakpoints: list[BreakpointRule] = Field(default_factory=list)
    error: Optional[str] = None
    pause_requested: bool = False
    processed_commands: list[str] = Field(default_factory=list)
    event_count: int = 0
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class RunSummary(BaseModel):
    id: str
    parent_run_id: Optional[str] = None
    task: str
    mode: str
    scenario_id: str
    status: RunStatus
    created_at: str
    updated_at: str
    metric_summary: str = ""
