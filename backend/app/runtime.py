"""Debug controller: valid state transitions, stepping, revisions, replay.

The high-level workflow is the program being debugged.  Tool operations are a
second, finer execution level.  A completed step is never re-executed by
inspection; revisions to completed work branch into a new run from a checkpoint.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .artifacts import ArtifactStore, EventLog, RunRepository
from .breakpoints import interpret_breakpoint, matching_breakpoint
from .config import LIVE_AVAILABLE, RUNS_DIR, live_status
from .dataset import OlistDataset
from .llm import (
    LLMClient,
    LLMError,
    LLMFormatError,
    LiveUnavailable,
    live_plan,
    live_revision,
)
from .models import (
    ApprovalRequest,
    BreakpointRule,
    Checkpoint,
    EvidenceRecord,
    Fact,
    FeedbackMessage,
    MetricValue,
    Operation,
    Report,
    RevisionChange,
    RevisionProposal,
    Run,
    RunSummary,
    SemanticBinding,
    StepUpdate,
    TaskSpecVersion,
    WorkflowStep,
    DependencyNode,
    utcnow,
)
from .references import reference_check_for_run
from .scenarios import (
    PlanDraft,
    ScenarioError,
    build_scripted_plan,
    build_scripted_revision,
)
from .tools import (
    OPERATION_SUMMARIES,
    REQUIRES_APPROVAL,
    ToolError,
    execute_tool,
)


class DebuggerError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


READ_ONLY_TOOLS = {"inspect_schema", "inspect_data_dictionary", "profile_column", "sample_records"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DebuggerService:
    def __init__(
        self,
        dataset: Optional[OlistDataset] = None,
        runs_dir: Path = RUNS_DIR,
        llm_client: Optional[LLMClient] = None,
    ):
        self.dataset = dataset or OlistDataset()
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.store = ArtifactStore(self.runs_dir / "artifacts")
        self.events = EventLog(self.runs_dir)
        self.repo = RunRepository(self.runs_dir)
        self.llm = llm_client or LLMClient()
        self.runs: dict[str, Run] = {}
        self._locks: dict[str, threading.RLock] = {}
        # Pause is delivered through a lock-free event: the worker may hold the
        # run lock for an entire data operation, and a developer must not have
        # to wait for that lock just to request a stop at the next boundary.
        self._pause_events: dict[str, threading.Event] = {}
        self._preview_cache: dict[str, tuple[str, list[dict[str, Any]]]] = {}
        self._sandbox_lock = threading.Lock()
        self._load_runs()

    # ------------------------------------------------------------------ setup
    def _load_runs(self) -> None:
        for run in self.repo.load_all():
            if run.status == "running":
                run.status = "failed"
                run.error = "The server restarted while this run was executing. Start a new run to continue."
                run.pause_requested = False
                self.repo.save(run)
            self.runs[run.id] = run
            self._locks[run.id] = threading.RLock()

    def _lock(self, run_id: str) -> threading.RLock:
        if run_id not in self._locks:
            self._locks[run_id] = threading.RLock()
        return self._locks[run_id]

    def _get(self, run_id: str) -> Run:
        if run_id not in self.runs:
            raise DebuggerError(404, "run_not_found", f"Run {run_id!r} does not exist.")
        return self.runs[run_id]

    def _persist(self, run: Run) -> None:
        run.updated_at = utcnow()
        self._preview_cache.pop(run.id, None)
        self.repo.save(run)

    def _event(self, run: Run, event_type: str, message: str, **fields: Any) -> None:
        run.event_count = self.events.append(run.id, event_type, message, **fields)

    def _record_command(self, run: Run, command_id: Optional[str]) -> None:
        if command_id:
            run.processed_commands.append(command_id)
            run.processed_commands = run.processed_commands[-100:]

    def _duplicate_command(self, run: Run, command_id: Optional[str]) -> Optional[dict[str, Any]]:
        if command_id and command_id in run.processed_commands:
            view = self._view(run)
            view["idempotent_replay"] = True
            return view
        return None

    # --------------------------------------------------------------- commands
    def create_run(self, task: str, mode: str, dataset_name: str = "olist") -> dict[str, Any]:
        task = (task or "").strip()
        if not task:
            raise DebuggerError(422, "invalid_task", "Enter a data task.")
        if len(task) > 4000:
            raise DebuggerError(422, "invalid_task", "Task text is too long.")
        if mode not in {"scripted", "live"}:
            raise DebuggerError(422, "invalid_mode", "Mode must be scripted or live.")
        if dataset_name not in {"olist", "sandbox"}:
            raise DebuggerError(422, "invalid_dataset", "Dataset must be olist or sandbox.")
        if dataset_name == "sandbox" and mode == "live":
            raise DebuggerError(422, "unsupported_mode", "Live Agent is not enabled for the disposable SQLite sandbox in this POC.")
        if not self.dataset.available:
            raise DebuggerError(
                503,
                "dataset_missing",
                f"Olist CSV files are missing from {self.dataset.data_dir}. Run scripts/prepare_data.py.",
            )

        if mode == "live":
            if not self.llm.available or not LIVE_AVAILABLE:
                reason = live_status()["reason"] if not LIVE_AVAILABLE else None
                raise DebuggerError(
                    503,
                    "live_unavailable",
                    reason
                    or (
                        "Live Agent is unavailable: no LLM API key is configured. "
                        "Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env."
                    ),
                )
            try:
                plan = live_plan(task, self.dataset, self.llm)
            except LiveUnavailable as exc:
                raise DebuggerError(503, "live_unavailable", str(exc)) from exc
            except LLMFormatError as exc:
                raise DebuggerError(422, "invalid_live_plan", str(exc)) from exc
            except LLMError as exc:
                raise DebuggerError(502, "llm_error", str(exc)) from exc
        else:
            try:
                plan = build_scripted_plan(task, dataset_name, self.dataset)
            except ScenarioError as exc:
                raise DebuggerError(422, "unsupported_task", str(exc)) from exc
            except Exception as exc:
                raise DebuggerError(500, "planning_error", f"Scripted planner failed: {exc}") from exc

        run = self._create_run_from_plan(task, mode, dataset_name, plan)
        with self._lock(run.id):
            if dataset_name == "sandbox":
                self._init_sandbox(run)
            self._event(run, "run_created", f"Run created in {mode} mode with {len(run.steps)} workflow step(s).", phase="created")
            for rule in run.breakpoints:
                self._event(
                    run,
                    "breakpoint_added",
                    f"Scenario guard active: {rule.label}",
                    breakpoint=rule.id,
                    operation_types=list(rule.operation_types),
                )
            self._event(run, "paused", "Paused at the first high-level step boundary.", step=run.steps[0].title)
            self._persist(run)
        return self.get_view(run.id)

    def _create_run_from_plan(self, task: str, mode: str, dataset_name: str, plan: PlanDraft) -> Run:
        run_id = _new_id("run")
        steps: list[WorkflowStep] = []
        for index, draft in enumerate(plan.steps, start=1):
            step_id = f"step_{index}"
            operations = self._make_operations(step_id, draft.get("operations") or [], index)
            chain = [
                DependencyNode(
                    label=str(node.get("label", "")),
                    step_id=node.get("step_id"),
                    status="PLANNED",
                )
                for node in (draft.get("dependency_chain") or [])
            ]
            steps.append(
                WorkflowStep(
                    id=step_id,
                    index=index - 1,
                    title=str(draft.get("title") or f"Step {index}"),
                    goal=str(draft.get("goal") or ""),
                    understanding=str(draft.get("understanding") or ""),
                    target_data=[str(x) for x in (draft.get("target_data") or [])][:4],
                    rationale=str(draft.get("rationale") or ""),
                    operations=operations,
                    dependency_chain=chain,
                )
            )
        if dataset_name == "sandbox":
            tables: dict[str, str] = {}
        else:
            tables = {name: f"src:{name}" for name in ("orders", "customers", "order_items", "order_payments", "products")}
        spec = TaskSpecVersion(
            id=_new_id("spec"),
            run_id=run_id,
            version=1,
            objective=plan.task_spec["objective"],
            population=plan.task_spec["population"],
            time_scope=plan.task_spec["time_scope"],
            metric_definitions=plan.task_spec["metric_definitions"],
            semantic_bindings=plan.task_spec["semantic_bindings"],
            assumptions=plan.task_spec["assumptions"],
        )
        checkpoint = Checkpoint(
            id=_new_id("ckpt"),
            boundary=0,
            label="Start of run",
            tables=dict(tables),
            task_spec_version=1,
        )
        run = Run(
            id=run_id,
            task=task,
            mode=mode,
            dataset=dataset_name,
            scenario_id=plan.scenario_id,
            reference_kind=plan.reference_kind,
            planner_label=plan.planner_label,
            status="paused_before_step",
            cursor_step=0,
            cursor_op=0,
            inside=False,
            steps=steps,
            tables=tables,
            task_specs=[spec],
            current_spec_version=1,
            checkpoints=[checkpoint],
        )
        for text in plan.initial_breakpoints:
            interpretation = interpret_breakpoint(text)
            if not interpretation.supported or interpretation.rule is None:
                continue
            draft = interpretation.rule
            run.breakpoints.append(
                BreakpointRule(
                    id=_new_id("bp"),
                    natural_language=draft.natural_language,
                    label=draft.label,
                    operation_types=list(draft.operation_types),
                    kind=draft.kind,
                    condition=(
                        draft.condition.model_copy(deep=True) if draft.condition else None
                    ),
                    phase=draft.phase,
                )
            )
        self._refresh_dependencies(run)
        self.runs[run.id] = run
        self._locks[run.id] = threading.RLock()
        self._persist(run)
        return run

    def _make_operations(self, step_id: str, drafts: list[dict[str, Any]], step_index: int) -> list[Operation]:
        operations = []
        for j, draft in enumerate(drafts, start=1):
            tool = str(draft.get("tool") or "")
            args = draft.get("args") or {}
            operations.append(
                Operation(
                    id=f"op_{step_index}_{j}",
                    step_id=step_id,
                    tool=tool,
                    args=args,
                    status="pending",
                    summary=OPERATION_SUMMARIES.get(tool, tool),
                    requires_approval=tool in REQUIRES_APPROVAL,
                )
            )
        return operations

    def _init_sandbox(self, run: Run) -> None:
        with self._sandbox_lock:
            template = self.runs_dir / "_sandbox_template.sqlite"
            if not template.exists():
                orders = self.dataset.table("orders")
                order_frame = orders.loc[
                    :, ["order_id", "customer_id", "order_status", "order_purchase_timestamp"]
                ].copy()
                order_frame["order_purchase_timestamp"] = order_frame[
                    "order_purchase_timestamp"
                ].astype(str)
                customers = self.dataset.table("customers")
                customer_frame = customers.loc[
                    :,
                    [
                        "customer_id",
                        "customer_unique_id",
                        "customer_zip_code_prefix",
                        "customer_city",
                        "customer_state",
                    ],
                ].copy()
                customer_frame["archived"] = 0
                customer_frame["archived_at"] = None
                conn = sqlite3.connect(template)
                try:
                    order_frame.to_sql("sandbox_orders", conn, index=False, if_exists="replace")
                    customer_frame.to_sql(
                        "sandbox_customers", conn, index=False, if_exists="replace"
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_sandbox_status ON sandbox_orders(order_status)"
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_sandbox_order_customer ON sandbox_orders(customer_id)"
                    )
                    conn.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sandbox_customer_id ON sandbox_customers(customer_id)"
                    )
                    conn.commit()
                finally:
                    conn.close()
            destination = self.runs_dir / run.id / "sandbox.sqlite"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template, destination)
            initial = next((c for c in run.checkpoints if c.boundary == 0), None)
            if initial is not None:
                initial.sandbox_snapshot = self._snapshot_sandbox(run, initial.id)

    def _sandbox_path(self, run: Run) -> Optional[str]:
        if run.dataset != "sandbox":
            return None
        path = self.runs_dir / run.id / "sandbox.sqlite"
        return str(path) if path.exists() else None

    def _snapshot_sandbox(self, run: Run, checkpoint_id: str) -> Optional[str]:
        """Copy the current sandbox into an immutable per-checkpoint snapshot."""
        if run.dataset != "sandbox":
            return None
        source = self.runs_dir / run.id / "sandbox.sqlite"
        if not source.exists():
            return None
        relative = Path("checkpoints") / f"{checkpoint_id}.sqlite"
        destination = self.runs_dir / run.id / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return str(relative)

    def _checkpoint_sandbox_path(self, run: Run, checkpoint: Checkpoint) -> Optional[Path]:
        if run.dataset != "sandbox" or not checkpoint.sandbox_snapshot:
            return None
        path = self.runs_dir / run.id / checkpoint.sandbox_snapshot
        return path if path.exists() else None

    # ------------------------------------------------------------- public read
    def list_runs(self) -> list[dict[str, Any]]:
        summaries = []
        for run in sorted(self.runs.values(), key=lambda r: r.updated_at, reverse=True):
            first_metric = next(iter(run.metrics.values()), None)
            summaries.append(
                RunSummary(
                    id=run.id,
                    parent_run_id=run.parent_run_id,
                    task=run.task,
                    mode=run.mode,
                    scenario_id=run.scenario_id,
                    status=run.status,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                    metric_summary=first_metric.formatted if first_metric else "",
                ).model_dump(mode="json")
            )
        return summaries

    def get_view(self, run_id: str) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            return self._view(run)

    def get_step_view(self, run_id: str, step_id: str) -> dict[str, Any]:
        view = self.get_view(run_id)
        for step in view["steps"]:
            if step["id"] == step_id:
                return step
        raise DebuggerError(404, "step_not_found", f"Step {step_id!r} does not exist.")

    # ------------------------------------------------------------------- view
    def _status_label(self, run: Run) -> str:
        if run.status == "paused_before_step":
            if run.cursor_step < len(run.steps):
                return f"Paused before step {run.cursor_step + 1}"
            return "Paused at end"
        if run.status == "paused_before_operation":
            return f"Inside step {run.cursor_step + 1} · before operation {run.cursor_op + 1}"
        if run.status == "awaiting_approval":
            if run.approval and run.approval.matched_breakpoint:
                if run.approval.mandatory:
                    return "Breakpoint hit · approval pending"
                return "Paused at breakpoint"
            return "Awaiting approval"
        if run.status == "running":
            return "Running"
        if run.status == "completed":
            return "Completed"
        if run.status == "failed":
            return "Failed"
        return run.status.capitalize()

    def _capabilities(self, run: Run) -> dict[str, Any]:
        paused = run.status in {"paused_before_step", "paused_before_operation"}
        has_child_for_revision = False
        if run.accepted_revision_id:
            has_child_for_revision = any(
                self.runs.get(child_id)
                and self.runs[child_id].replay_of_revision_id == run.accepted_revision_id
                for child_id in run.child_run_ids
            )
        return {
            "step_into": paused,
            "step_over": paused,
            "step_out": run.status == "paused_before_operation" and run.inside,
            "continue": paused,
            "pause": run.status == "running",
            "replay": bool(run.accepted_revision_id) and not has_child_for_revision,
            "compare": bool(run.child_run_ids),
            "feedback": run.status in {"paused_before_step", "paused_before_operation", "completed"},
            "approve": run.status == "awaiting_approval",
            "reject": run.status == "awaiting_approval",
        }

    def _preview_current_step(self, run: Run) -> list[dict[str, Any]]:
        if run.status not in {"paused_before_step", "paused_before_operation"}:
            return []
        if run.cursor_step >= len(run.steps):
            return []
        step = run.steps[run.cursor_step]
        if step.status in {"completed", "failed", "skipped"}:
            return []
        if run.dataset == "sandbox":
            return []
        fingerprint = json.dumps(
            {
                "updated": run.updated_at,
                "step": step.id,
                "cursor_op": run.cursor_op,
                "ops": [[op.id, op.status] for op in step.operations],
                "tables": run.tables,
            },
            sort_keys=True,
        )
        cached = self._preview_cache.get(run.id)
        if cached and cached[0] == fingerprint:
            return cached[1]
        frames = self._frames(run)
        metrics = dict(run.metrics)
        preview: list[dict[str, Any]] = []
        for op in step.operations:
            must_pause, _ = self._operation_gate(run, op)
            if op.status != "pending" or must_pause:
                continue
            try:
                result = execute_tool(
                    op.tool,
                    op.args,
                    frames,
                    self.dataset,
                    metrics,
                    sandbox_path=None,
                    preview=True,
                )
            except ToolError as exc:
                preview.append(
                    {
                        "id": f"preview_error_{op.id}",
                        "operation_id": op.id,
                        "kind": "preview_error",
                        "status": "PREVIEW",
                        "label": "PREVIEW ERROR",
                        "summary": f"Proposed operation cannot execute on the current data: {exc.message}",
                        "facts": [{"label": "Error", "value": exc.message}],
                        "details": {"code": exc.code},
                        "source_artifact_ids": [],
                        "created_at": utcnow(),
                    }
                )
                break
            for draft in result.evidence:
                preview.append(
                    {
                        "id": f"preview_{op.id}_{len(preview)}",
                        "operation_id": op.id,
                        "kind": draft.kind,
                        "status": "PREVIEW",
                        "label": f"PREVIEW · {draft.label}",
                        "summary": draft.summary,
                        "facts": [f.model_dump() for f in draft.facts],
                        "details": draft.details,
                        "source_artifact_ids": [],
                        "created_at": utcnow(),
                    }
                )
            frames.update(result.updates)
            if result.metric:
                metrics[result.metric.name] = result.metric
        self._preview_cache[run.id] = (fingerprint, preview)
        return preview

    def _view(self, run: Run) -> dict[str, Any]:
        self._refresh_dependencies(run)
        preview = self._preview_current_step(run)
        current_spec = run.task_specs[-1]
        steps = []
        for step in run.steps:
            evidence = [ev.model_dump(mode="json") for op in step.operations for ev in op.evidence]
            if step.index == run.cursor_step and preview:
                evidence.extend(preview)
            if run.approval and run.approval.step_id == step.id:
                approval_facts = [f.model_dump() for f in run.approval.facts]
                approval_details: dict[str, Any] = {
                    "args": run.approval.args,
                    "mandatory": run.approval.mandatory,
                }
                approval_label = f"RISKY WRITE · {run.approval.tool}"
                if run.approval.matched_breakpoint:
                    matched = run.approval.matched_breakpoint
                    approval_facts.insert(
                        0, {"label": "Breakpoint", "value": matched.label}
                    )
                    approval_details["matched_breakpoint"] = matched.model_dump(mode="json")
                    approval_label = f"BREAKPOINT · {run.approval.tool}"
                evidence.append(
                    {
                        "id": f"approval_{run.approval.id}",
                        "operation_id": run.approval.operation_id,
                        "kind": "approval_preview",
                        "status": "PREVIEW",
                        "label": approval_label,
                        "summary": run.approval.label,
                        "facts": approval_facts,
                        "details": approval_details,
                        "source_artifact_ids": [],
                        "created_at": run.approval.created_at,
                    }
                )
            is_current = step.index == run.cursor_step
            display_status = step.status
            if is_current:
                if run.status == "awaiting_approval":
                    display_status = "awaiting_approval"
                elif run.status == "paused_before_operation":
                    display_status = "inside"
                elif run.status == "paused_before_step":
                    display_status = "paused"
                elif run.status == "running":
                    display_status = "running"
            step_messages = [m.model_dump(mode="json") for m in run.messages if m.step_id == step.id]
            step_proposals = [
                p.model_dump(mode="json") for p in run.revisions if p.step_id == step.id
            ]
            steps.append(
                {
                    "id": step.id,
                    "index": step.index,
                    "number": step.index + 1,
                    "title": step.title,
                    "goal": step.goal,
                    "status": display_status,
                    "base_status": step.status,
                    "inherited": step.inherited,
                    "is_current": is_current,
                    "is_inside": is_current and run.inside,
                    "is_read_only": step.status in {"completed", "skipped"},
                    "is_skipped": step.status == "skipped",
                    "understanding": step.understanding,
                    "target_data": step.target_data,
                    "rationale": step.rationale,
                    "operations": [
                        {
                            "id": op.id,
                            "tool": op.tool,
                            "args": op.args,
                            "status": op.status,
                            "summary": op.summary,
                            "requires_approval": op.requires_approval,
                            "input_artifact_ids": op.input_artifact_ids,
                            "output_artifact_ids": op.output_artifact_ids,
                            "error": op.error,
                            "evidence": [ev.model_dump(mode="json") for ev in op.evidence],
                        }
                        for op in step.operations
                    ],
                    "evidence": evidence,
                    "preview_evidence": preview if is_current else [],
                    "dependency_chain": [node.model_dump(mode="json") for node in step.dependency_chain],
                    "messages": step_messages,
                    "proposals": step_proposals,
                    "active_proposal": next(
                        (p for p in reversed(step_proposals) if p["status"] in {"pending", "no_change"}),
                        None,
                    ),
                    "can_feedback": run.status in {"paused_before_step", "paused_before_operation", "completed"},
                }
            )
        return {
            "id": run.id,
            "parent_run_id": run.parent_run_id,
            "replay_of_revision_id": run.replay_of_revision_id,
            "task": run.task,
            "mode": run.mode,
            "dataset": run.dataset,
            "scenario_id": run.scenario_id,
            "reference_kind": run.reference_kind,
            "planner_label": run.planner_label,
            "status": run.status,
            "status_label": self._status_label(run),
            "cursor": {"step_index": run.cursor_step, "op_index": run.cursor_op, "inside": run.inside},
            "steps": steps,
            "task_spec": current_spec.model_dump(mode="json"),
            "task_spec_history": [s.model_dump(mode="json") for s in run.task_specs],
            "metrics": {k: v.model_dump(mode="json") for k, v in run.metrics.items()},
            "report": run.report.model_dump(mode="json") if run.report else None,
            "reference_check": run.reference_check,
            "revisions": [p.model_dump(mode="json") for p in run.revisions],
            "messages": [m.model_dump(mode="json") for m in run.messages],
            "accepted_revision_id": run.accepted_revision_id,
            "child_run_ids": run.child_run_ids,
            "approval": run.approval.model_dump(mode="json") if run.approval else None,
            "approval_history": [a.model_dump(mode="json") for a in run.approvals],
            "breakpoints": [rule.model_dump(mode="json") for rule in run.breakpoints],
            "capabilities": self._capabilities(run),
            "error": run.error,
            "event_count": run.event_count,
            "events_tail": self.events.tail(run.id, 12),
            "idempotent_replay": False,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }

    def _refresh_dependencies(self, run: Run) -> None:
        by_id = {step.id: step for step in run.steps}
        for step in run.steps:
            for node in step.dependency_chain:
                target = by_id.get(node.step_id or "")
                node.status = "OBSERVED" if target and target.status == "completed" else "PLANNED"

    # ------------------------------------------------------------- execution
    def _frames(self, run: Run) -> dict[str, Any]:
        return {name: self.store.get_frame(artifact_id, self.dataset) for name, artifact_id in run.tables.items()}

    def _checkpoint_snapshot(self, run: Run, boundary: int, label: str, spec_version: int) -> Checkpoint:
        checkpoint_id = _new_id("ckpt")
        checkpoint = Checkpoint(
            id=checkpoint_id,
            boundary=boundary,
            label=label,
            tables=dict(run.tables),
            task_spec_version=spec_version,
            sandbox_snapshot=self._snapshot_sandbox(run, checkpoint_id),
        )
        run.checkpoints.append(checkpoint)
        return checkpoint

    def _evidence_records(self, run: Run, step: WorkflowStep, op: Operation, result, source_ids: list[str]) -> list[EvidenceRecord]:
        records = []
        for draft in result.evidence:
            record = EvidenceRecord(
                id=_new_id("ev"),
                operation_id=op.id,
                kind=draft.kind,
                status="OBSERVED",
                label=draft.label,
                summary=draft.summary,
                facts=draft.facts,
                details=draft.details,
                source_artifact_ids=list(dict.fromkeys(source_ids)),
            )
            records.append(record)
        return records

    def _execute_operation_now(self, run: Run, step: WorkflowStep, op: Operation) -> None:
        op.started_at = utcnow()
        op.status = "pending"
        op.error = None
        frames = self._frames(run)
        try:
            result = execute_tool(
                op.tool,
                op.args,
                frames,
                self.dataset,
                run.metrics,
                sandbox_path=self._sandbox_path(run),
                preview=False,
            )
        except ToolError as exc:
            op.status = "failed"
            op.error = exc.message
            op.finished_at = utcnow()
            run.status = "failed"
            run.error = f"Operation {op.id} ({op.tool}) failed: {exc.message}"
            self._event(run, "operation_failed", run.error, step=step.title, operation=op.id, code=exc.code)
            raise DebuggerError(500, "operation_failed", run.error) from exc
        except Exception as exc:
            op.status = "failed"
            op.error = str(exc)
            op.finished_at = utcnow()
            run.status = "failed"
            run.error = f"Operation {op.id} ({op.tool}) failed: {exc}"
            self._event(run, "operation_failed", run.error, step=step.title, operation=op.id)
            raise DebuggerError(500, "operation_failed", run.error) from exc

        input_ids = []
        for name in result.inputs:
            artifact_id = run.tables.get(name)
            if artifact_id and artifact_id not in input_ids:
                input_ids.append(artifact_id)
        output_ids = []
        for name, dataframe in result.updates.items():
            artifact_id = self.store.put_frame(dataframe, run.id, f"{step.title} / {op.tool} / {name}")
            run.tables[name] = artifact_id
            output_ids.append(artifact_id)
        op.input_artifact_ids = input_ids
        op.output_artifact_ids = output_ids
        op.finished_at = utcnow()
        op.status = "completed"
        op.summary = result.summary or op.summary
        source_ids = input_ids + output_ids
        op.evidence = self._evidence_records(run, step, op, result, source_ids)
        op.evidence_ids = [ev.id for ev in op.evidence]
        if result.metric:
            metric = result.metric.model_copy(deep=True)
            metric.source_step_id = step.id
            output_name = op.args.get("output") if isinstance(op.args, dict) else None
            if output_name and output_name in run.tables:
                metric.source_artifact_id = run.tables[output_name]
            run.metrics[metric.name] = metric
        if result.report:
            report = result.report.model_copy(deep=True)
            step_titles = {s.id: s.title for s in run.steps}
            report.step_links = []
            seen = set()
            for metric in report.metrics:
                if metric.source_step_id and metric.source_step_id not in seen:
                    seen.add(metric.source_step_id)
                    report.step_links.append(
                        {"step_id": metric.source_step_id, "title": step_titles.get(metric.source_step_id, "")}
                    )
            if run.approvals:
                decision = run.approvals[-1]
                if decision.status == "rejected":
                    prefix = "The destructive operation was rejected at the approval gate; no rows were changed. "
                    note = f"Approval {decision.id} was rejected: {decision.reason or 'no reason recorded'}."
                else:
                    prefix = "The destructive operation was approved and executed exactly once. "
                    note = f"Approval {decision.id} was granted by the developer."
                if not report.summary.startswith(prefix):
                    report.summary = prefix + report.summary
                if note not in report.notes:
                    report.notes.append(note)
            run.report = report
        self._event(
            run,
            "operation_completed",
            f"{op.tool} completed in {step.title}.",
            step=step.title,
            operation=op.id,
            outputs=output_ids,
        )

    def _operation_gate(self, run: Run, op: Operation) -> tuple[bool, Optional[BreakpointRule]]:
        """Return whether the operation must pause and the matching user rule.

        The mandatory destructive-write policy is always checked. A configured
        breakpoint can add a gate but can never remove or weaken the mandatory
        one.
        """
        matched = matching_breakpoint(run, op)
        return op.tool in REQUIRES_APPROVAL or matched is not None, matched

    def _pause_for_approval(
        self,
        run: Run,
        step: WorkflowStep,
        op: Operation,
        matched_breakpoint: Optional[BreakpointRule] = None,
    ) -> None:
        mandatory = op.tool in REQUIRES_APPROVAL
        facts: list[Fact]
        if mandatory:
            frames = self._frames(run)
            try:
                result = execute_tool(
                    op.tool,
                    op.args,
                    frames,
                    self.dataset,
                    run.metrics,
                    sandbox_path=self._sandbox_path(run),
                    preview=True,
                )
            except ToolError as exc:
                op.status = "failed"
                op.error = exc.message
                run.status = "failed"
                run.error = f"Cannot prepare approval preview: {exc.message}"
                self._event(run, "approval_preview_failed", run.error, step=step.title, operation=op.id)
                raise DebuggerError(500, "approval_preview_failed", run.error) from exc
            facts = result.evidence[0].facts if result.evidence else []
            summary = result.evidence[0].summary if result.evidence else "Risky write requires approval."
        else:
            # Breakpoint-only gates never call the tool. There is no generic
            # preview contract for arbitrary tools, and a pause must not execute.
            facts = [
                Fact(label="Operation", value=op.tool),
                Fact(label="Executed", value="No"),
            ]
            summary = f"Breakpoint matched before {op.tool}; the operation has not executed."

        approval = ApprovalRequest(
            id=_new_id("approval"),
            operation_id=op.id,
            step_id=step.id,
            tool=op.tool,
            args=op.args,
            label=summary,
            facts=facts,
            matched_breakpoint=(
                matched_breakpoint.model_copy(deep=True) if matched_breakpoint else None
            ),
            mandatory=mandatory,
        )
        op.status = "awaiting_approval"
        op.requires_approval = True
        run.approval = approval
        run.status = "awaiting_approval"
        run.cursor_step = step.index
        run.cursor_op = step.operations.index(op)
        run.inside = True
        message = summary
        if matched_breakpoint:
            message = f"Breakpoint matched ({matched_breakpoint.label}) — {summary}"
        self._event(
            run,
            "awaiting_approval",
            message,
            step=step.title,
            operation=op.id,
            approval=approval.id,
            breakpoint=matched_breakpoint.id if matched_breakpoint else None,
            mandatory=mandatory,
        )

    def _complete_step(self, run: Run, step: WorkflowStep, keep_running: bool = False, status: str = "completed") -> None:
        step.status = status  # type: ignore[assignment]
        self._checkpoint_snapshot(
            run,
            boundary=step.index + 1,
            label=f"After step {step.index + 1}: {step.title}",
            spec_version=run.current_spec_version,
        )
        run.cursor_step = step.index + 1
        run.cursor_op = 0
        run.inside = False
        self._event(run, "step_completed", f"Completed step {step.index + 1}: {step.title}.", step=step.title)
        if run.cursor_step >= len(run.steps):
            run.status = "completed"
            self._event(run, "run_completed", "Run completed.")
            self._finalize_run(run)
        elif keep_running:
            run.status = "running"
        else:
            run.status = "paused_before_step"
            self._event(run, "paused", f"Paused before step {run.cursor_step + 1}.", step=run.steps[run.cursor_step].title)

    def _finalize_run(self, run: Run) -> None:
        if run.report is None and run.metrics:
            metrics = list(run.metrics.values())
            step_titles = {s.id: s.title for s in run.steps}
            links = []
            seen = set()
            for metric in metrics:
                if metric.source_step_id and metric.source_step_id not in seen:
                    seen.add(metric.source_step_id)
                    links.append({"step_id": metric.source_step_id, "title": step_titles.get(metric.source_step_id, "")})
            run.report = Report(
                title=run.task[:120],
                summary="Run completed. " + ", ".join(f"{m.name} = {m.formatted}" for m in metrics) + ".",
                metrics=metrics,
                notes=["The plan did not include a compose_report operation; this is a controller-generated summary."],
                step_links=links,
            )
        try:
            run.reference_check = reference_check_for_run(run, self.store, self.dataset)
        except Exception as exc:
            run.reference_check = {
                "error": f"Reference check could not be computed: {exc}",
                "agreement": False,
            }

    def _advance_one_operation(self, run: Run, step: WorkflowStep) -> str:
        pending = [op for op in step.operations if op.status == "pending"]
        if not pending:
            self._complete_step(run, step)
            return "step_completed"
        op_index = step.operations.index(pending[0])
        op = pending[0]
        must_pause, matched = self._operation_gate(run, op)
        if must_pause:
            self._pause_for_approval(run, step, op, matched)
            return "awaiting_approval"
        self._execute_operation_now(run, step, op)
        more = [o for o in step.operations if o.status == "pending"]
        if more:
            run.cursor_step = step.index
            run.cursor_op = step.operations.index(more[0])
            run.inside = True
            run.status = "paused_before_operation"
            self._event(run, "paused", f"Paused before next operation in {step.title}.", step=step.title, operation=more[0].id)
            return "paused_before_operation"
        self._complete_step(run, step)
        return "step_completed"

    def _run_step_remaining(self, run: Run, step: WorkflowStep, pause_check=None, keep_running: bool = False) -> str:
        while True:
            pending = [op for op in step.operations if op.status == "pending"]
            if not pending:
                self._complete_step(run, step, keep_running=keep_running)
                return "step_completed"
            op = pending[0]
            must_pause, matched = self._operation_gate(run, op)
            if must_pause:
                self._pause_for_approval(run, step, op, matched)
                return "awaiting_approval"
            self._execute_operation_now(run, step, op)
            if pause_check and pause_check():
                more = [o for o in step.operations if o.status == "pending"]
                if more:
                    run.cursor_step = step.index
                    run.cursor_op = step.operations.index(more[0])
                    run.inside = True
                    run.status = "paused_before_operation"
                else:
                    self._complete_step(run, step)
                return "paused"
            more = [o for o in step.operations if o.status == "pending"]
            if more:
                run.cursor_step = step.index
                run.cursor_op = step.operations.index(more[0])
                run.inside = True
                if keep_running:
                    run.status = "running"
                else:
                    run.status = "paused_before_operation"

    # ----------------------------------------------------------- step commands
    def step_into(self, run_id: str, command_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            if run.status == "running":
                raise DebuggerError(409, "run_running", "Pause the run before stepping.")
            if run.status == "awaiting_approval":
                raise DebuggerError(409, "approval_pending", "Resolve the pending approval before stepping.")
            if run.status not in {"paused_before_step", "paused_before_operation"}:
                raise DebuggerError(409, "invalid_state", "Step Into is only available while the run is paused.")
            step = run.steps[run.cursor_step] if run.cursor_step < len(run.steps) else None
            if step is None:
                raise DebuggerError(409, "invalid_state", "There is no current step to enter.")
            if run.status == "paused_before_step":
                if not step.operations:
                    # Interpretation-only steps have no operation context to
                    # enter; Step Into completes the semantic boundary.
                    self._event(
                        run,
                        "step_into",
                        f"Step {step.index + 1} ({step.title}) has no data operations; completed the interpretation boundary.",
                        step=step.title,
                    )
                    self._complete_step(run, step)
                    self._record_command(run, command_id)
                    self._persist(run)
                    return self._view(run)
                run.inside = True
                pending = [op for op in step.operations if op.status == "pending"]
                run.cursor_op = step.operations.index(pending[0]) if pending else 0
                run.status = "paused_before_operation"
                self._event(
                    run,
                    "step_into",
                    f"Entered step {step.index + 1}: {step.title}. No operation has been executed by this Step Into.",
                    step=step.title,
                )
                self._record_command(run, command_id)
                self._persist(run)
                return self._view(run)
            # Already inside a step: Step Into advances exactly one operation boundary.
            if not step.operations:
                self._complete_step(run, step)
                self._record_command(run, command_id)
                self._persist(run)
                return self._view(run)
            outcome = self._advance_one_operation(run, step)
            self._event(run, "step_into_operation", f"Advanced one operation inside {step.title} ({outcome}).", step=step.title)
            self._record_command(run, command_id)
            self._persist(run)
            return self._view(run)

    def step_over(self, run_id: str, command_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            if run.status == "running":
                raise DebuggerError(409, "run_running", "Pause the run before stepping.")
            if run.status == "awaiting_approval":
                raise DebuggerError(409, "approval_pending", "Resolve the pending approval before stepping.")
            if run.status not in {"paused_before_step", "paused_before_operation"}:
                raise DebuggerError(409, "invalid_state", "Step Over is only available while the run is paused.")
            if run.cursor_step >= len(run.steps):
                raise DebuggerError(409, "invalid_state", "The run has no current step.")
            step = run.steps[run.cursor_step]
            self._event(run, "step_over", f"Executing step {step.index + 1}: {step.title}.", step=step.title)
            self._run_step_remaining(run, step)
            self._record_command(run, command_id)
            self._persist(run)
            return self._view(run)

    def step_out(self, run_id: str, command_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            if run.status != "paused_before_operation" or not run.inside:
                raise DebuggerError(409, "invalid_state", "Step Out is only available while inside a step.")
            if run.cursor_step >= len(run.steps):
                raise DebuggerError(409, "invalid_state", "The run has no current step.")
            step = run.steps[run.cursor_step]
            self._event(run, "step_out", f"Leaving step {step.index + 1} after executing remaining operations.", step=step.title)
            self._run_step_remaining(run, step)
            self._record_command(run, command_id)
            self._persist(run)
            return self._view(run)

    def continue_run(self, run_id: str, command_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            if run.status == "awaiting_approval":
                raise DebuggerError(409, "approval_pending", "Continue cannot bypass a pending destructive-write approval.")
            if run.status not in {"paused_before_step", "paused_before_operation"}:
                raise DebuggerError(409, "invalid_state", "Continue is only available while the run is paused.")
            run.status = "running"
            run.pause_requested = False
            pause_event = threading.Event()
            self._pause_events[run.id] = pause_event
            self._event(run, "continue", "Continue requested; running until completion, pause, or a mandatory approval.")
            self._record_command(run, command_id)
            self._persist(run)
            thread = threading.Thread(
                target=self._continue_worker,
                args=(run.id, pause_event),
                name=f"continue-{run.id}",
                daemon=True,
            )
            thread.start()
            return self._view(run)

    def pause(self, run_id: str, command_id: Optional[str] = None) -> dict[str, Any]:
        # Signal the worker before taking the state lock.  A data operation may
        # hold that lock for seconds; the request itself must still arrive.
        active_event = self._pause_events.get(run_id)
        current = self.runs.get(run_id)
        if active_event is not None and current is not None and current.status == "running":
            active_event.set()
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            if active_event is None:
                raise DebuggerError(409, "invalid_state", "Pause is only available while the run is executing.")
            if run.status == "running":
                run.pause_requested = True
                self._event(run, "pause_requested", "Pause requested; the controller will stop at the next safe boundary.")
                self._persist(run)
            elif run.status not in {"paused_before_step", "paused_before_operation"}:
                raise DebuggerError(409, "invalid_state", "Pause is only available while the run is executing.")
        # Wait (outside the run lock) for the worker to reach a safe boundary, so
        # the command's response reflects the actual paused state.
        deadline = time.time() + 30
        while time.time() < deadline:
            current = self.runs.get(run_id)
            if current is None or current.status != "running":
                break
            time.sleep(0.01)
        with self._lock(run_id):
            run = self._get(run_id)
            if command_id and command_id not in run.processed_commands:
                self._record_command(run, command_id)
                self._persist(run)
            return self._view(run)

    def _finish_pause(self, run: Run, pause_event: threading.Event) -> None:
        """Park a running run at an operation/step boundary after a pause."""
        pause_event.clear()
        self._pause_events.pop(run.id, None)
        run.pause_requested = False
        if run.status == "completed":
            return
        if run.cursor_step < len(run.steps):
            step = run.steps[run.cursor_step]
            pending = [op for op in step.operations if op.status == "pending"]
            if pending:
                run.cursor_step = step.index
                run.cursor_op = step.operations.index(pending[0])
                run.inside = True
                run.status = "paused_before_operation"
            else:
                run.inside = False
                run.status = "paused_before_step"
        else:
            run.inside = False
            run.status = "paused_before_step"
        self._event(run, "paused", "Paused at the next safe boundary.")
        self._persist(run)

    def _continue_worker(self, run_id: str, pause_event: threading.Event) -> None:
        delay = float(__import__("os").getenv("DEBUGGER_OPERATION_DELAY", "0") or 0)
        while True:
            with self._lock(run_id):
                run = self.runs.get(run_id)
                if run is None or run.status != "running":
                    self._pause_events.pop(run_id, None)
                    return
                if pause_event.is_set() or run.pause_requested:
                    self._finish_pause(run, pause_event)
                    return
                if run.cursor_step >= len(run.steps):
                    run.status = "completed"
                    self._finalize_run(run)
                    self._pause_events.pop(run_id, None)
                    self._persist(run)
                    return
                step = run.steps[run.cursor_step]
                pending = [op for op in step.operations if op.status == "pending"]
                if not pending:
                    self._complete_step(run, step, keep_running=True)
                    self._persist(run)
                    continue
                op = pending[0]
                must_pause, matched = self._operation_gate(run, op)
                if must_pause:
                    try:
                        self._pause_for_approval(run, step, op, matched)
                    except DebuggerError:
                        pass
                    self._pause_events.pop(run_id, None)
                    self._persist(run)
                    return
                try:
                    if delay:
                        time.sleep(delay)
                    self._execute_operation_now(run, step, op)
                except DebuggerError:
                    self._pause_events.pop(run_id, None)
                    self._persist(run)
                    return
                more = [o for o in step.operations if o.status == "pending"]
                if more:
                    run.cursor_step = step.index
                    run.cursor_op = step.operations.index(more[0])
                    run.inside = True
                    run.status = "running"
                else:
                    self._complete_step(run, step, keep_running=True)
                if pause_event.is_set() or run.pause_requested:
                    self._finish_pause(run, pause_event)
                    return
                self._persist(run)

    # --------------------------------------------------------------- approval
    def approve(self, run_id: str, command_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            if run.status != "awaiting_approval" or run.approval is None:
                raise DebuggerError(409, "invalid_state", "There is no pending approval for this run.")
            approval = run.approval
            step = next((s for s in run.steps if s.id == approval.step_id), None)
            op = next((o for o in (step.operations if step else []) if o.id == approval.operation_id), None)
            if step is None or op is None:
                raise DebuggerError(500, "approval_broken", "The pending approval references a missing operation.")
            try:
                self._execute_operation_now(run, step, op)
            finally:
                run.approval = None
            approval.status = "approved"
            run.approvals.append(approval)
            run.status = "paused_before_operation"
            run.cursor_step = step.index
            run.inside = True
            more = [o for o in step.operations if o.status == "pending"]
            if more:
                run.cursor_op = step.operations.index(more[0])
            else:
                self._complete_step(run, step)
            self._event(run, "approval_granted", "Developer approved the destructive operation; it executed exactly once.", step=step.title, operation=op.id)
            self._record_command(run, command_id)
            self._persist(run)
            return self._view(run)

    def reject_approval(self, run_id: str, reason: str = "", command_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            if run.status != "awaiting_approval" or run.approval is None:
                raise DebuggerError(409, "invalid_state", "There is no pending approval for this run.")
            approval = run.approval
            step = next((s for s in run.steps if s.id == approval.step_id), None)
            op = next((o for o in (step.operations if step else []) if o.id == approval.operation_id), None)
            if step is None or op is None:
                raise DebuggerError(500, "approval_broken", "The pending approval references a missing operation.")
            op.status = "rejected"
            op.finished_at = utcnow()
            rejected_details: dict[str, Any] = {"approval_id": approval.id, "reason": reason}
            rejected_label = "RISKY WRITE · rejected"
            if approval.matched_breakpoint:
                rejected_details["breakpoint_id"] = approval.matched_breakpoint.id
                rejected_label = "BREAKPOINT · rejected"
            op.evidence = [
                EvidenceRecord(
                    id=_new_id("ev"),
                    operation_id=op.id,
                    kind="approval_rejected",
                    status="OBSERVED",
                    label=rejected_label,
                    summary="Developer rejected the destructive operation. No rows were changed.",
                    facts=[Fact(label="Executed", value="No"), Fact(label="Reason", value=reason or "Rejected by developer")],
                    details=rejected_details,
                )
            ]
            op.evidence_ids = [ev.id for ev in op.evidence]
            approval.status = "rejected"
            approval.reason = reason or "Rejected by developer"
            run.approvals.append(approval)
            run.approval = None
            self._event(run, "approval_rejected", "Developer rejected the destructive operation; it was not executed.", step=step.title, operation=op.id)
            self._complete_step(run, step, status="skipped")
            self._record_command(run, command_id)
            self._persist(run)
            return self._view(run)

    # ------------------------------------------------------------ breakpoints
    def interpret_breakpoint(self, run_id: str, text: str) -> dict[str, Any]:
        self._get(run_id)
        return interpret_breakpoint(text).model_dump(mode="json")

    def add_breakpoint(
        self, run_id: str, text: str, command_id: Optional[str] = None
    ) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            interpretation = interpret_breakpoint(text)
            if not interpretation.supported or interpretation.rule is None:
                raise DebuggerError(422, "unsupported_breakpoint", interpretation.message)
            draft = interpretation.rule
            existing = next(
                (
                    rule
                    for rule in run.breakpoints
                    if rule.active
                    and rule.phase == draft.phase
                    and rule.kind == draft.kind
                    and rule.operation_types == draft.operation_types
                    and rule.condition == draft.condition
                ),
                None,
            )
            if existing is not None:
                existing.natural_language = draft.natural_language
                existing.label = draft.label
                rule = existing
                event_type = "breakpoint_updated"
            else:
                rule = BreakpointRule(
                    id=_new_id("bp"),
                    natural_language=draft.natural_language,
                    label=draft.label,
                    operation_types=list(draft.operation_types),
                    kind=draft.kind,
                    condition=draft.condition.model_copy(deep=True) if draft.condition else None,
                    phase=draft.phase,
                )
                run.breakpoints.append(rule)
                event_type = "breakpoint_added"
            self._event(
                run,
                event_type,
                f"Breakpoint active: {rule.label}",
                breakpoint=rule.id,
                kind=rule.kind,
                operation_types=list(rule.operation_types),
                condition=rule.condition.model_dump(mode="json") if rule.condition else None,
            )
            self._record_command(run, command_id)
            self._persist(run)
            return self._view(run)

    def remove_breakpoint(
        self,
        run_id: str,
        breakpoint_id: str,
        command_id: Optional[str] = None,
    ) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            rule = next((item for item in run.breakpoints if item.id == breakpoint_id), None)
            if rule is None:
                raise DebuggerError(
                    404, "breakpoint_not_found", f"Breakpoint {breakpoint_id!r} does not exist."
                )
            run.breakpoints.remove(rule)
            pending_at_rule = bool(
                run.approval
                and run.approval.matched_breakpoint
                and run.approval.matched_breakpoint.id == breakpoint_id
            )
            message = f"Breakpoint removed: {rule.label}"
            if pending_at_rule:
                message += " The pending gate remains in force."
            self._event(
                run,
                "breakpoint_removed",
                message,
                breakpoint=breakpoint_id,
                pending_gate=pending_at_rule,
            )
            self._record_command(run, command_id)
            self._persist(run)
            return self._view(run)

    def set_breakpoint_active(
        self,
        run_id: str,
        breakpoint_id: str,
        active: bool,
        command_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Enable or disable a stored rule without changing its definition.

        The controller already ignores inactive rules in ``matching_breakpoint``,
        so this only flips the existing ``active`` flag. A gate that is already
        open keeps its snapshot and remains enforceable.
        """
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            rule = next((item for item in run.breakpoints if item.id == breakpoint_id), None)
            if rule is None:
                raise DebuggerError(
                    404, "breakpoint_not_found", f"Breakpoint {breakpoint_id!r} does not exist."
                )
            if rule.active != active:
                rule.active = active
                pending_at_rule = bool(
                    run.approval
                    and run.approval.matched_breakpoint
                    and run.approval.matched_breakpoint.id == breakpoint_id
                )
                message = f"Breakpoint {'enabled' if active else 'disabled'}: {rule.label}"
                if pending_at_rule:
                    message += " The pending gate remains in force."
                self._event(
                    run,
                    "breakpoint_enabled" if active else "breakpoint_disabled",
                    message,
                    breakpoint=breakpoint_id,
                    pending_gate=pending_at_rule,
                )
            self._record_command(run, command_id)
            self._persist(run)
            return self._view(run)

    # --------------------------------------------------------------- feedback
    def _user_message(self, run: Run, step_id: str, text: str) -> FeedbackMessage:
        message = FeedbackMessage(id=_new_id("msg"), step_id=step_id, role="user", text=text)
        run.messages.append(message)
        return message

    def _agent_message(self, run: Run, step_id: str, text: str, proposal_id: Optional[str] = None) -> FeedbackMessage:
        message = FeedbackMessage(id=_new_id("msg"), step_id=step_id, role="agent", text=text, proposal_id=proposal_id)
        run.messages.append(message)
        return message

    def _revision_changes(self, run: Run, proposal: RevisionProposal) -> tuple[list[RevisionChange], bool, int]:
        current = run.task_specs[-1]
        changes: list[RevisionChange] = []
        old_bindings = {b.concept: b for b in current.semantic_bindings}
        new_bindings = {b.concept: b for b in (proposal.proposed_bindings or current.semantic_bindings)}
        for concept, new in new_bindings.items():
            old = old_bindings.get(concept)
            if old is None or old.source_table != new.source_table or old.column != new.column:
                changes.append(
                    RevisionChange(
                        kind="binding",
                        description=f"{concept} binding changed.",
                        before=f"{old.source_table}.{old.column}" if old else "not specified",
                        after=f"{new.source_table}.{new.column}",
                    )
                )
        old_metrics = {m.name: m for m in current.metric_definitions}
        new_metrics = {m.name: m for m in (proposal.proposed_metrics or current.metric_definitions)}
        for name, new in new_metrics.items():
            old = old_metrics.get(name)
            if old is None or old.definition != new.definition or old.unit != new.unit:
                changes.append(
                    RevisionChange(
                        kind="metric",
                        description=f"Metric definition changed for {name}.",
                        before=old.definition if old else "not specified",
                        after=new.definition,
                    )
                )
        if proposal.proposed_assumptions is not None and proposal.proposed_assumptions != current.assumptions:
            changes.append(
                RevisionChange(
                    kind="assumption",
                    description="Task assumptions updated.",
                    before="; ".join(current.assumptions),
                    after="; ".join(proposal.proposed_assumptions),
                )
            )
        execution_changed = False
        earliest = 10**9
        for update in proposal.step_updates:
            step = next((s for s in run.steps if s.id == update.step_id), None)
            if step is None:
                continue
            if update.operations is not None:
                old_ops = [(o.tool, json.dumps(o.args, sort_keys=True)) for o in step.operations]
                new_ops = [(o.get("tool"), json.dumps(o.get("args") or {}, sort_keys=True)) for o in update.operations]
                if old_ops != new_ops:
                    execution_changed = True
                    changes.append(
                        RevisionChange(
                            kind="plan",
                            description=f"Operations changed for step {step.index + 1} ({step.title}).",
                            before=" → ".join(o.tool for o in step.operations) or "no operations",
                            after=" → ".join(str(o.get("tool")) for o in update.operations) or "no operations",
                        )
                    )
            if any(
                [
                    update.title not in (None, step.title),
                    update.goal not in (None, step.goal),
                    update.understanding not in (None, step.understanding),
                    update.target_data is not None and update.target_data != step.target_data,
                    update.rationale not in (None, step.rationale),
                ]
            ):
                if not any(c.kind == "plan" and str(step.index + 1) in c.description for c in changes):
                    changes.append(
                        RevisionChange(
                            kind="plan",
                            description=f"Step {step.index + 1} ({step.title}) explanation or target fields updated.",
                            before=step.understanding,
                            after=update.understanding or step.understanding,
                        )
                    )
            if update.operations is not None or update.understanding is not None:
                earliest = min(earliest, step.index)
        if earliest == 10**9:
            selected = next((s for s in run.steps if s.id == proposal.step_id), None)
            earliest = selected.index if selected else 0
        return changes, execution_changed, earliest

    def feedback(self, run_id: str, step_id: str, text: str, command_id: Optional[str] = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise DebuggerError(422, "empty_feedback", "Enter feedback for the agent.")
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            if run.status == "running":
                raise DebuggerError(409, "run_running", "Pause the run before sending feedback.")
            breakpoint_gate = bool(
                run.status == "awaiting_approval"
                and run.approval is not None
                and run.approval.matched_breakpoint is not None
            )
            if run.status == "awaiting_approval" and not breakpoint_gate:
                raise DebuggerError(409, "approval_pending", "Resolve the pending approval with Approve or Reject before sending feedback.")
            if run.status not in {"paused_before_step", "paused_before_operation", "completed", "awaiting_approval"}:
                raise DebuggerError(409, "invalid_state", "Feedback is unavailable in the current run state.")
            step = next((s for s in run.steps if s.id == step_id), None)
            if step is None:
                raise DebuggerError(404, "step_not_found", f"Step {step_id!r} does not exist.")
            if breakpoint_gate and run.approval is not None and step.id != run.approval.step_id:
                raise DebuggerError(
                    409,
                    "breakpoint_step_mismatch",
                    "Feedback at a breakpoint gate must concern the paused step.",
                )
            if breakpoint_gate and run.approval is not None and run.approval.matched_breakpoint is not None:
                self._event(
                    run,
                    "breakpoint_feedback",
                    "Feedback recorded at the breakpoint gate; the proposed operation remains unexecuted.",
                    step=step.title,
                    operation=run.approval.operation_id,
                    breakpoint=run.approval.matched_breakpoint.id,
                )
            self._user_message(run, step_id, text)
            draft: dict[str, Any]
            if run.mode == "scripted":
                try:
                    draft = build_scripted_revision(run, step, text, self.dataset)
                except ScenarioError as exc:
                    proposal = RevisionProposal(
                        id=_new_id("rev"),
                        run_id=run.id,
                        step_id=step.id,
                        user_text=text,
                        explanation=str(exc),
                        status="no_change",
                        earliest_affected_step=step.index,
                    )
                    self._agent_message(run, step.id, proposal.explanation, proposal.id)
                    run.revisions.append(proposal)
                    self._event(run, "revision_no_change", proposal.explanation, step=step.title)
                    self._record_command(run, command_id)
                    self._persist(run)
                    return self._view(run)
                except Exception as exc:
                    self._event(run, "scripted_revision_failed", str(exc), step=step.title)
                    self._persist(run)
                    raise DebuggerError(500, "revision_failed", f"Scripted revision generator failed: {exc}") from exc
            else:
                frames = self._frames(run)
                try:
                    draft = live_revision(run, step, text, self.dataset, frames, self.llm)
                except LiveUnavailable as exc:
                    self._event(run, "live_revision_failed", str(exc), step=step.title)
                    self._persist(run)
                    raise DebuggerError(503, "live_unavailable", str(exc)) from exc
                except (LLMError, LLMFormatError) as exc:
                    self._event(run, "live_revision_failed", str(exc), step=step.title)
                    self._persist(run)
                    raise DebuggerError(502, "llm_error", str(exc)) from exc
            proposal = RevisionProposal(
                id=_new_id("rev"),
                run_id=run.id,
                step_id=step.id,
                user_text=text,
                explanation=str(draft.get("explanation") or ""),
                changes=draft.get("changes") or [],
                proposed_bindings=draft.get("proposed_bindings"),
                proposed_metrics=draft.get("proposed_metrics"),
                proposed_assumptions=draft.get("proposed_assumptions"),
                step_updates=draft.get("step_updates") or [],
                investigations=draft.get("investigations") or [],
                earliest_affected_step=int(draft.get("earliest_affected_step") or 0),
                status="pending",
            )
            computed_changes, execution_changed, earliest = self._revision_changes(run, proposal)
            if not proposal.changes:
                proposal.changes = computed_changes
            proposal.earliest_affected_step = min(proposal.earliest_affected_step, earliest) if proposal.step_updates else earliest
            proposal.execution_changed = execution_changed
            if not proposal.changes:
                proposal.status = "no_change"
            self._agent_message(run, step.id, proposal.explanation, proposal.id)
            run.revisions.append(proposal)
            self._event(
                run,
                "revision_proposed",
                f"Agent proposed a revision for step {step.index + 1} ({len(proposal.changes)} change(s)).",
                step=step.title,
                revision=proposal.id,
                execution_changed=execution_changed,
            )
            self._record_command(run, command_id)
            self._persist(run)
            return self._view(run)

    # --------------------------------------------------------------- revision
    def _find_proposal(self, run: Run, revision_id: str) -> RevisionProposal:
        proposal = next((p for p in run.revisions if p.id == revision_id), None)
        if proposal is None:
            raise DebuggerError(404, "revision_not_found", f"Revision {revision_id!r} does not exist.")
        return proposal

    @staticmethod
    def _operation_output_names(operations: list[Operation]) -> list[str]:
        names = []
        for operation in operations:
            output = operation.args.get("output") if isinstance(operation.args, dict) else None
            if isinstance(output, str) and output:
                names.append(output)
        return names

    @classmethod
    def _remap_table_references(cls, value: Any, mapping: dict[str, str], *, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {name: cls._remap_table_references(item, mapping, key=name) for name, item in value.items()}
        if isinstance(value, list):
            return [cls._remap_table_references(item, mapping) for item in value]
        if key in {"table", "left", "right", "output"} and isinstance(value, str):
            return mapping.get(value, value)
        return value

    def _apply_step_updates(
        self,
        run: Run,
        proposal: RevisionProposal,
        steps: list[WorkflowStep],
        previous_spec: TaskSpecVersion | None = None,
    ) -> set[str]:
        changed_columns: set[str] = set()
        updates = sorted(
            proposal.step_updates,
            key=lambda item: next((step.index for step in steps if step.id == item.step_id), 10**9),
        )
        output_mappings: list[tuple[int, dict[str, str]]] = []
        for update in updates:
            step = next((s for s in steps if s.id == update.step_id), None)
            if step is None:
                continue
            if update.title is not None:
                step.title = update.title
            if update.goal is not None:
                step.goal = update.goal
            if update.understanding is not None:
                step.understanding = update.understanding
            if update.target_data is not None:
                step.target_data = update.target_data
            if update.rationale is not None:
                step.rationale = update.rationale
            if update.operations is not None:
                previous_outputs = self._operation_output_names(step.operations)
                step.operations = self._make_operations(step.id, update.operations, step.index + 1)
                current_outputs = self._operation_output_names(step.operations)
                replacements = (
                    {
                        old: new
                        for old, new in zip(previous_outputs, current_outputs)
                        if old != new
                    }
                    if len(previous_outputs) == len(current_outputs)
                    else {}
                )
                if replacements:
                    output_mappings.append((step.index, replacements))
                step.status = "pending"
        # A model may rename a revised step's output tables without repeating every
        # downstream operation. Repair those references after all updates have been
        # applied, so operations replaced by a later step_update are covered too.
        for source_index, replacements in output_mappings:
            for later_step in steps[source_index + 1:]:
                for operation in later_step.operations:
                    operation.args = self._remap_table_references(operation.args, replacements)
        if proposal.proposed_metrics:
            baseline_spec = previous_spec if previous_spec is not None else run.task_specs[-1]
            current_metrics = {metric.name: metric for metric in baseline_spec.metric_definitions}
            revised_metrics = {
                metric.name: metric
                for metric in proposal.proposed_metrics
                if metric.name not in current_metrics
                or metric.definition != current_metrics[metric.name].definition
                or metric.unit != current_metrics[metric.name].unit
            }
            for step in steps:
                if step.status == "completed":
                    continue
                for operation in step.operations:
                    if operation.tool != "compute_metric":
                        continue
                    revised = revised_metrics.get(str(operation.args.get("name") or ""))
                    if revised is not None:
                        operation.args = {
                            **operation.args,
                            "definition": revised.definition,
                            "unit": revised.unit,
                        }
        return changed_columns

    def _update_dependency_labels(self, run: Run, proposal: RevisionProposal, start_index: int) -> None:
        # Compare against the spec *before* the accepted proposal was appended.
        previous = run.task_specs[-2] if len(run.task_specs) > 1 else run.task_specs[-1]
        new_bindings = proposal.proposed_bindings
        if not new_bindings:
            return
        old_bindings = {b.concept: b for b in previous.semantic_bindings}
        replacements = []
        for new in new_bindings:
            old = old_bindings.get(new.concept)
            if old and old.column != new.column and old.column:
                replacements.append((old.column, new.column))
        if not replacements:
            return
        for step in run.steps[start_index:]:
            for node in step.dependency_chain:
                for old_col, new_col in replacements:
                    node.label = node.label.replace(old_col, new_col)

    def _build_proposed_spec(self, run: Run, proposal: RevisionProposal, version: int) -> TaskSpecVersion:
        current = run.task_specs[-1]
        return TaskSpecVersion(
            id=_new_id("spec"),
            run_id=run.id,
            version=version,
            objective=current.objective,
            population=current.population,
            time_scope=current.time_scope,
            metric_definitions=proposal.proposed_metrics or current.metric_definitions,
            semantic_bindings=proposal.proposed_bindings or current.semantic_bindings,
            assumptions=proposal.proposed_assumptions if proposal.proposed_assumptions is not None else current.assumptions,
        )

    def _validate_revised_plan(self, run: Run, steps: list[WorkflowStep], affected: int) -> list[str]:
        checkpoint = next((c for c in reversed(run.checkpoints) if c.boundary == affected), None)
        if checkpoint is None:
            return [f"No checkpoint is available at boundary {affected}."]
        frames: dict[str, Any] = {}
        metrics: dict[str, MetricValue] = {}
        issues: list[str] = []
        sandbox_path: Optional[str] = None
        temporary_sandbox: Optional[Path] = None
        if run.dataset == "sandbox":
            snapshot = self._checkpoint_sandbox_path(run, checkpoint)
            if snapshot is None:
                return [
                    f"No sandbox snapshot is available at checkpoint boundary {affected}."
                ]
            temporary_sandbox = (
                self.runs_dir / run.id / f"validation_{uuid.uuid4().hex[:10]}.sqlite"
            )
            shutil.copyfile(snapshot, temporary_sandbox)
            sandbox_path = str(temporary_sandbox)
        else:
            frames = {
                name: self.store.get_frame(artifact_id, self.dataset)
                for name, artifact_id in checkpoint.tables.items()
            }
        try:
            for step in steps[affected:]:
                for op in step.operations:
                    must_pause, _ = self._operation_gate(run, op)
                    if must_pause:
                        continue
                    try:
                        result = execute_tool(
                            op.tool,
                            op.args,
                            frames,
                            self.dataset,
                            metrics,
                            sandbox_path=sandbox_path,
                        )
                    except ToolError as exc:
                        issues.append(
                            f"Step {step.index + 1} ({step.title}) / {op.tool}: {exc.message}"
                        )
                        return issues
                    frames.update(result.updates)
                    if result.metric:
                        metrics[result.metric.name] = result.metric
            return issues
        finally:
            if temporary_sandbox is not None:
                temporary_sandbox.unlink(missing_ok=True)

    def accept_revision(
        self, run_id: str, revision_id: str, command_id: Optional[str] = None
    ) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            if run.status == "running":
                raise DebuggerError(409, "run_running", "Pause the run before accepting a revision.")
            proposal = self._find_proposal(run, revision_id)
            if proposal.status != "pending":
                raise DebuggerError(409, "invalid_revision_state", f"Revision is {proposal.status}, not pending.")
            if not proposal.changes:
                raise DebuggerError(422, "no_changes", "This proposal contains no changes.")
            if not proposal.execution_changed:
                raise DebuggerError(
                    422,
                    "no_execution_change",
                    "This proposal does not change any data operations. It would only alter labels or notes, "
                    "so it cannot be accepted as an execution revision.",
                )
            affected = max(0, min(proposal.earliest_affected_step, len(run.steps) - 1))
            affected_step = run.steps[affected]
            ops_executed_in_affected = any(
                op.status in {"completed", "awaiting_approval", "rejected"} for op in affected_step.operations
            )
            requires_replay = affected < run.cursor_step or ops_executed_in_affected
            if requires_replay:
                proposal.status = "accepted"
                run.accepted_revision_id = proposal.id
                self._agent_message(
                    run,
                    proposal.step_id,
                    "Revision accepted. Because Step "
                    f"{affected + 1} already has executed operations, the original run stays immutable and Replay "
                    "will branch from the checkpoint before that step.",
                    proposal.id,
                )
                self._event(run, "revision_accepted", "Revision accepted; replay required.", revision=proposal.id, replay=True)
                self._record_command(run, command_id)
                self._persist(run)
                return self._view(run)

            # Apply in place only when no affected operation has run yet.
            revised_steps = [step.model_copy(deep=True) for step in run.steps]
            self._apply_step_updates(run, proposal, revised_steps, previous_spec=run.task_specs[-1])
            issues = self._validate_revised_plan(run, revised_steps, affected)
            if issues:
                raise DebuggerError(422, "invalid_revision_plan", "The revised plan does not execute: " + "; ".join(issues[:3]))
            new_spec = self._build_proposed_spec(run, proposal, run.current_spec_version + 1)
            run.task_specs.append(new_spec)
            run.current_spec_version = new_spec.version
            # Rebuild steps with repaired operations; reset cursor to the earliest affected boundary.
            run.steps = revised_steps
            for step in run.steps[affected:]:
                if step.id in {u.step_id for u in proposal.step_updates if u.operations is not None}:
                    step.status = "pending"
            run.cursor_step = affected
            run.cursor_op = 0
            run.inside = False
            run.status = "paused_before_step"
            self._update_dependency_labels(run, proposal, affected)
            proposal.status = "accepted"
            self._agent_message(run, proposal.step_id, "Revision accepted and applied to the pending workflow.", proposal.id)
            self._event(run, "revision_accepted", "Revision accepted and applied in place.", revision=proposal.id, replay=False)
            self._record_command(run, command_id)
            self._persist(run)
            return self._view(run)

    def reject_revision(self, run_id: str, revision_id: str, command_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            proposal = self._find_proposal(run, revision_id)
            if proposal.status != "pending":
                raise DebuggerError(409, "invalid_revision_state", f"Revision is {proposal.status}, not pending.")
            proposal.status = "rejected"
            self._agent_message(run, proposal.step_id, "Revision rejected. The recorded interpretation and plan are unchanged.", proposal.id)
            self._event(run, "revision_rejected", "Revision rejected by developer.", revision=proposal.id)
            self._record_command(run, command_id)
            self._persist(run)
            return self._view(run)

    # ------------------------------------------------------------------ replay
    def replay(self, run_id: str, revision_id: Optional[str] = None, command_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self._get(run_id)
            duplicate = self._duplicate_command(run, command_id)
            if duplicate:
                return duplicate
            revision_id = revision_id or run.accepted_revision_id
            if not revision_id:
                raise DebuggerError(409, "replay_unavailable", "Replay is only available after an accepted revision to completed work.")
            proposal = self._find_proposal(run, revision_id)
            if proposal.status != "accepted":
                raise DebuggerError(409, "replay_unavailable", "The revision has not been accepted.")
            existing = next(
                (
                    self.runs[cid]
                    for cid in run.child_run_ids
                    if cid in self.runs and self.runs[cid].replay_of_revision_id == proposal.id
                ),
                None,
            )
            if existing:
                return {"run": self._view(existing), "comparison": self.compare_runs(run.id, existing.id)}
            affected = max(0, min(proposal.earliest_affected_step, len(run.steps) - 1))
            checkpoint = next((c for c in reversed(run.checkpoints) if c.boundary == affected), None)
            if checkpoint is None:
                raise DebuggerError(500, "checkpoint_missing", f"No checkpoint exists at boundary {affected}.")
            child = self._create_replay_child(run, proposal, checkpoint, affected)
            self._event(run, "replay_created", f"Created replay branch {child.id} from boundary {affected}.", revision=proposal.id, child=child.id)
            run.child_run_ids.append(child.id)
            self._persist(run)
            try:
                self._run_to_completion_sync(child)
            finally:
                self._persist(child)
            comparison = self.compare_runs(run.id, child.id)
            self._record_command(run, command_id)
            self._persist(run)
            return {"run": self._view(child), "comparison": comparison}

    def _create_replay_child(self, parent: Run, proposal: RevisionProposal, checkpoint: Checkpoint, affected: int) -> Run:
        child_id = _new_id("run")
        steps = []
        for parent_step in parent.steps:
            step = parent_step.model_copy(deep=True)
            if step.index < affected:
                step.inherited = True
                step.status = "completed"
            else:
                step.status = "pending"
                step.inherited = False
                for op in step.operations:
                    op.status = "pending"
                    op.evidence = []
                    op.evidence_ids = []
                    op.input_artifact_ids = []
                    op.output_artifact_ids = []
                    op.error = None
                    op.started_at = None
                    op.finished_at = None
            steps.append(step)
        proposed_spec = self._build_proposed_spec(parent, proposal, parent.current_spec_version + 1)
        proposed_spec.run_id = child_id
        checkpoint_child = Checkpoint(
            id=_new_id("ckpt"),
            boundary=affected,
            label=f"Replay checkpoint before step {affected + 1}",
            tables=dict(checkpoint.tables),
            task_spec_version=proposed_spec.version,
        )
        child = Run(
            id=child_id,
            parent_run_id=parent.id,
            replay_of_revision_id=proposal.id,
            task=parent.task,
            mode=parent.mode,
            dataset=parent.dataset,
            scenario_id=parent.scenario_id,
            reference_kind=parent.reference_kind,
            planner_label=parent.planner_label + " · replay branch",
            status="paused_before_step",
            cursor_step=affected,
            cursor_op=0,
            inside=False,
            steps=steps,
            tables=dict(checkpoint.tables),
            task_specs=[s.model_copy(deep=True) for s in parent.task_specs] + [proposed_spec],
            current_spec_version=proposed_spec.version,
            checkpoints=[checkpoint_child],
            breakpoints=[
                BreakpointRule(
                    id=_new_id("bp"),
                    natural_language=rule.natural_language,
                    label=rule.label,
                    operation_types=list(rule.operation_types),
                    kind=rule.kind,
                    condition=(
                        rule.condition.model_copy(deep=True) if rule.condition else None
                    ),
                    phase=rule.phase,
                    active=rule.active,
                )
                for rule in parent.breakpoints
            ],
        )
        if parent.dataset == "sandbox":
            snapshot = self._checkpoint_sandbox_path(parent, checkpoint)
            if snapshot is None:
                raise DebuggerError(
                    500,
                    "sandbox_checkpoint_missing",
                    f"No sandbox snapshot is available at checkpoint boundary {affected}.",
                )
            destination = self.runs_dir / child.id / "sandbox.sqlite"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(snapshot, destination)
            checkpoint_child.sandbox_snapshot = self._snapshot_sandbox(
                child, checkpoint_child.id
            )
        self._apply_step_updates(child, proposal, child.steps, previous_spec=parent.task_specs[-1])
        for step in child.steps[affected:]:
            step.status = "pending"
        self._update_dependency_labels(child, proposal, affected)
        child.messages.append(
            FeedbackMessage(
                id=_new_id("msg"),
                step_id=child.steps[affected].id,
                role="system",
                text=(
                    f"Replay branch from {parent.id} after accepted revision {proposal.id}. "
                    f"Steps 1–{affected} are inherited; step {affected + 1} and later are re-executed from the checkpoint."
                ),
            )
        )
        self.runs[child.id] = child
        self._locks[child.id] = threading.RLock()
        self._event(child, "replay_started", f"Replay branch from {parent.id}; re-executing from step {affected + 1}.")
        self._persist(child)
        return child

    def _run_to_completion_sync(self, run: Run) -> None:
        with self._lock(run.id):
            run.status = "running"
            while True:
                if run.cursor_step >= len(run.steps):
                    run.status = "completed"
                    self._finalize_run(run)
                    self._event(run, "run_completed", "Replay branch completed.")
                    self._persist(run)
                    return
                step = run.steps[run.cursor_step]
                pending = [op for op in step.operations if op.status == "pending"]
                if not pending:
                    self._complete_step(run, step, keep_running=True)
                    self._persist(run)
                    continue
                op = pending[0]
                must_pause, matched = self._operation_gate(run, op)
                if must_pause:
                    self._pause_for_approval(run, step, op, matched)
                    self._persist(run)
                    return
                try:
                    self._execute_operation_now(run, step, op)
                except DebuggerError:
                    self._persist(run)
                    return
                more = [o for o in step.operations if o.status == "pending"]
                if more:
                    run.cursor_step = step.index
                    run.cursor_op = step.operations.index(more[0])
                    run.inside = True
                    run.status = "running"
                else:
                    self._complete_step(run, step, keep_running=True)
                self._persist(run)

    # -------------------------------------------------------------- comparison
    def compare_runs(self, parent_id: str, child_id: Optional[str] = None) -> dict[str, Any]:
        parent = self._get(parent_id)
        if child_id:
            child = self._get(child_id)
        else:
            child = next(
                (self.runs[cid] for cid in reversed(parent.child_run_ids) if cid in self.runs),
                None,
            )
            if child is None:
                raise DebuggerError(404, "no_replay", "No replay branch exists for this run.")
        revision = next((r for r in parent.revisions if r.id == child.replay_of_revision_id), None)
        affected = revision.earliest_affected_step if revision else 0
        old_spec = parent.task_specs[-1]
        new_spec = child.task_specs[-1]
        binding_diff = []
        concepts = {b.concept: b for b in old_spec.semantic_bindings}
        concepts.update({b.concept: b for b in new_spec.semantic_bindings})
        for concept in concepts:
            old = next((b for b in old_spec.semantic_bindings if b.concept == concept), None)
            new = next((b for b in new_spec.semantic_bindings if b.concept == concept), None)
            if old is None or new is None or old.source_table != new.source_table or old.column != new.column:
                binding_diff.append(
                    {
                        "concept": concept,
                        "old": f"{old.source_table}.{old.column}" if old else "—",
                        "new": f"{new.source_table}.{new.column}" if new else "—",
                    }
                )
        metric_diff = []
        metric_names = {m.name for m in old_spec.metric_definitions} | {m.name for m in new_spec.metric_definitions} | set(parent.metrics) | set(child.metrics)
        for name in sorted(metric_names):
            old_metric = parent.metrics.get(name)
            new_metric = child.metrics.get(name)
            old_def = next((m for m in old_spec.metric_definitions if m.name == name), None)
            new_def = next((m for m in new_spec.metric_definitions if m.name == name), None)
            metric_diff.append(
                {
                    "name": name,
                    "old_value": old_metric.value if old_metric else None,
                    "old_formatted": old_metric.formatted if old_metric else "—",
                    "new_value": new_metric.value if new_metric else None,
                    "new_formatted": new_metric.formatted if new_metric else "—",
                    "old_definition": old_def.definition if old_def else None,
                    "new_definition": new_def.definition if new_def else None,
                }
            )
        plan_diff = []
        for index in range(min(len(parent.steps), len(child.steps))):
            if index < affected:
                continue
            old_step = parent.steps[index]
            new_step = child.steps[index]
            old_ops = [f"{op.tool} {json.dumps(op.args, sort_keys=True)}" for op in old_step.operations]
            new_ops = [f"{op.tool} {json.dumps(op.args, sort_keys=True)}" for op in new_step.operations]
            if old_ops != new_ops or old_step.title != new_step.title or old_step.understanding != new_step.understanding:
                plan_diff.append(
                    {
                        "step_index": index,
                        "step_number": index + 1,
                        "title": new_step.title,
                        "old_operations": [op.tool for op in old_step.operations],
                        "new_operations": [op.tool for op in new_step.operations],
                        "changed_operations": old_ops != new_ops,
                        "old_understanding": old_step.understanding,
                        "new_understanding": new_step.understanding,
                    }
                )
        invalidated = [
            {
                "step_number": step.index + 1,
                "title": step.title,
                "operations": [op.tool for op in step.operations],
            }
            for step in parent.steps[affected:]
        ]
        return {
            "parent_run_id": parent.id,
            "child_run_id": child.id,
            "revision_id": revision.id if revision else None,
            "affected_from_step": affected + 1,
            "parent_status": parent.status,
            "child_status": child.status,
            "binding_diff": binding_diff,
            "metric_definitions_diff": metric_diff,
            "plan_diff": plan_diff,
            "invalidated": invalidated,
            "parent_metrics": {k: v.model_dump(mode="json") for k, v in parent.metrics.items()},
            "child_metrics": {k: v.model_dump(mode="json") for k, v in child.metrics.items()},
            "parent_reference_check": parent.reference_check,
            "child_reference_check": child.reference_check,
            "child_report": child.report.model_dump(mode="json") if child.report else None,
            "revision_explanation": revision.explanation if revision else None,
            "revision_changes": [c.model_dump(mode="json") for c in revision.changes] if revision else [],
        }

    # ------------------------------------------------------------------ config
    def config(self) -> dict[str, Any]:
        status = live_status()
        return {
            "live": status,
            "scripted": {"available": True},
            "datasets": [
                {"id": "olist", "label": "Olist E-commerce"},
                {"id": "sandbox", "label": "SQLite Sandbox (disposable)"},
            ],
            "scenarios": [
                {"id": "repeat_purchase", "label": "Repeat purchase rate (Case A)"},
                {"id": "category_sales", "label": "Monthly category sales (Case B)"},
                {"id": "sandbox_cleanup", "label": "Sandbox risky DELETE"},
                {"id": "customer_cleanup", "label": "Customer cleanup (Case C)"},
            ],
            "default_task": "What is our customer repeat purchase rate?",
            "version": "0.9.0",
        }
