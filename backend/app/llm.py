"""Provider adapter for Live Agent planning and revision.

Live mode makes real HTTP calls to a configured LLM.  The model returns explicit
JSON records; this controller validates them and never executes model-supplied
code or SQL.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from .config import LLM_MODEL, LLM_PROVIDER, OPENAI_BASE_URL
from .dataset import OlistDataset
from .models import Fact, Investigation, MetricDefinition, SemanticBinding, StepUpdate
from .scenarios import PlanDraft
from .tools import TOOL_SPECS, ToolError, execute_tool

READ_ONLY_TOOLS = {"inspect_schema", "inspect_data_dictionary", "profile_column", "sample_records"}
LIVE_TOOL_NAMES = {t["name"] for t in TOOL_SPECS} - {
    "sandbox_inspect",
    "sandbox_delete_rows",
    "sandbox_customer_preview",
    "sandbox_delete_inactive_customers",
    "sandbox_archive_inactive_customers",
    "sandbox_customer_audit",
}


class LiveUnavailable(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class LLMFormatError(LLMError):
    pass


class LLMClient:
    def __init__(self, provider: str | None = None, model: str | None = None):
        self.provider = provider or LLM_PROVIDER
        self.model = model or LLM_MODEL
        if self.provider == "anthropic":
            self.api_key = os.getenv("ANTHROPIC_API_KEY")
        elif self.provider == "openai":
            self.api_key = os.getenv("OPENAI_API_KEY")
        else:
            self.api_key = None

    @property
    def available(self) -> bool:
        return bool(self.provider and self.api_key)

    def _post(self, system: str, user: str, max_tokens: int = 5000, json_mode: bool = False) -> str:
        if not self.available:
            raise LiveUnavailable(
                "Live Agent is unavailable: no LLM API key is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env."
            )
        try:
            with httpx.Client(timeout=120.0) as client:
                if self.provider == "anthropic":
                    response = client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": self.api_key or "",
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": self.model,
                            "max_tokens": max_tokens,
                            "temperature": 0,
                            "system": system,
                            "messages": [{"role": "user", "content": user}],
                        },
                    )
                else:
                    payload: dict[str, Any] = {
                        "model": self.model,
                        "temperature": 0,
                        "max_tokens": max_tokens,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                    }
                    if json_mode:
                        payload["response_format"] = {"type": "json_object"}
                    response = client.post(
                        f"{OPENAI_BASE_URL}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key or ''}",
                            "content-type": "application/json",
                        },
                        json=payload,
                    )
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        if response.status_code >= 400:
            body = response.text[:600]
            raise LLMError(f"LLM provider returned HTTP {response.status_code}: {body}")
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("LLM provider returned a non-JSON response.") from exc
        if self.provider == "anthropic":
            blocks = data.get("content") or []
            text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
        else:
            choices = data.get("choices") or []
            if not choices:
                raise LLMError("LLM provider returned no choices.")
            text = (choices[0].get("message") or {}).get("content") or ""
        if not text.strip():
            raise LLMError("LLM provider returned an empty response.")
        return text

    def complete_json(self, system: str, user: str, max_tokens: int = 5000) -> dict[str, Any]:
        try:
            text = self._post(system, user, max_tokens=max_tokens, json_mode=True)
        except LLMError as exc:
            # Reasoning-heavy responses can occasionally exhaust the completion
            # budget before emitting content. Retry once before surfacing the error.
            if "empty response" not in str(exc).lower():
                raise
            text = self._post(system, user, max_tokens=max_tokens, json_mode=True)
        try:
            return parse_json_object(text)
        except LLMFormatError as first_error:
            retry_user = (
                user
                + "\n\nThe previous response could not be parsed as JSON. Return one valid JSON "
                "object only, with no markdown fences, commentary, or trailing text."
            )
            retry_text = self._post(system, retry_user, max_tokens=max_tokens, json_mode=True)
            try:
                return parse_json_object(retry_text)
            except LLMFormatError as retry_error:
                raise LLMFormatError(
                    f"{first_error} A retry also failed: {retry_error}"
                ) from retry_error


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMFormatError("The LLM response did not contain a JSON object.")
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMFormatError(f"The LLM response was not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMFormatError("The LLM response JSON must be an object.")
    return value


def schema_context(dataset: OlistDataset) -> dict[str, Any]:
    out = {}
    for table in ("orders", "customers", "order_items", "order_payments", "products"):
        schema = dataset.schema(table)
        out[table] = {
            "rows": schema["rows"],
            "note": schema["note"],
            "columns": [
                {
                    "name": c["name"],
                    "dtype": c["dtype"],
                    "nulls": c["nulls"],
                    "unique": c["unique"],
                    "semantics": c["semantics"],
                }
                for c in schema["columns"]
            ],
        }
    return out


PLAN_SYSTEM = """You are the planning module of Data Agent Debugger, a research prototype.
Produce an explicit, inspectable interpretation and a short high-level workflow. Do not reveal private reasoning.
Use only the supplied tools and real columns. The controller validates and may reject invalid plans.
Return one JSON object only, with no markdown fences.

Output schema:
{
  "supported": true,
  "unsupported_reason": "",
  "task_spec": {
    "objective": "one sentence",
    "population": "explicit row population",
    "time_scope": "explicit time scope",
    "metric_definitions": [{"name": "snake_case", "definition": "explicit formula and grain", "unit": "percent|currency|number"}],
    "semantic_bindings": [{"concept": "business concept", "source_table": "table", "column": "column", "note": "why this binding"}],
    "assumptions": ["explicit assumption"]
  },
  "workflow": {
    "planner_note": "one sentence",
    "steps": [
      {
        "title": "short high-level stage",
        "goal": "one sentence",
        "understanding": "1-3 concise sentences: the first stage interprets the request; later stages explain that stage's purpose relative to the goal",
        "target_data": ["tables, key fields, operation, output - max 4 short lines"],
        "rationale": "one short sentence",
        "operations": [{"tool": "tool_name", "args": {}}]
      }
    ]
  }
}

Rules:
- Return 3 to 5 workflow steps. Use the smallest number that covers the task.
- Each step is a broad stage a developer can inspect as a unit. Put related filters,
  joins, groupings, metric calculations, and report generation together inside the
  appropriate stage.
- Never create a step for a single tool call or for one filter/join/group/metric
  operation. If adjacent steps belong to the same phase, merge them.
- Use short, task-specific titles (2 to 6 words). Do not reuse a fixed template;
  choose the stages that fit this task.
- Begin with a request-interpretation stage. Restate only the user's objective,
  intended metric, scope, and proposed business assumptions. Write user requirements
  as "The request asks..." and agent assumptions as "I am assuming..."; do not name
  database tables or columns, joins, filters, or implementation choices in this
  stage. It should contain no tool operations; later stages carry the work.
- For every later stage, the understanding explains why the stage exists in relation
  to the user's goal. Introduce data sources, schema mappings, and concrete operations
  when that stage first needs them, especially Explore Data and Prepare Data. Keep
  each understanding to 1-3 sentences; do not restate target_data or tool arguments.
- A stage may contain several controlled tool operations. Step Into exposes those
  operations individually.
- Example granularity for a repeat-purchase task: Understand Task → Explore Data
  → Prepare Customer Data (filter delivered orders, join customers, group/count)
  → Compute Repeat Purchase Rate → Generate Report. This illustrates grouping,
  not a required template; adapt the titles and stage count to the task.
- Not every task needs five stages. Use 3 or 4 when the work is simpler, and
  never pad the workflow with extra stages.
- Every table-producing operation must use a new, unique output table name.
- Do not use arbitrary SQL, Python, sandbox, or destructive tools.
- In target_data and operations, cite exact source columns. Do not invent columns.
- A report step should use compose_report only for results that exist. For a scalar
  result, include a compute_metric operation first and pass its exact `name` in
  metric_names. For a table-shaped result, such as monthly sales by category, pass the
  grouped output table in `table`; a grouped table is not a metric and must not be
  placed in metric_names. When a table also has a meaningful scalar total, compute that
  total with compute_metric from the grouped table and include its exact name too.
- If the task cannot be supported by these tools, set supported=false and give a short unsupported_reason; leave workflow.steps empty.
"""


def _normalise_task_spec(payload: dict[str, Any]) -> dict[str, Any]:
    metric_defs = []
    for item in payload.get("metric_definitions", []) or []:
        if not isinstance(item, dict) or not item.get("name") or not item.get("definition"):
            raise LLMFormatError("Each metric definition needs name and definition.")
        unit = item.get("unit", "number")
        if unit not in {"percent", "currency", "number"}:
            unit = "number"
        metric_defs.append(
            MetricDefinition(
                name=str(item["name"]),
                definition=str(item["definition"]),
                unit=unit,
                status="proposed",
            )
        )
    bindings = []
    for item in payload.get("semantic_bindings", []) or []:
        if not isinstance(item, dict) or not item.get("concept") or not item.get("source_table") or not item.get("column"):
            raise LLMFormatError("Each semantic binding needs concept, source_table, and column.")
        bindings.append(
            SemanticBinding(
                concept=str(item["concept"]),
                source_table=str(item["source_table"]),
                column=str(item["column"]),
                status="proposed",
                note=str(item.get("note", "")),
            )
        )
    assumptions = [str(a) for a in (payload.get("assumptions") or [])][:8]
    return {
        "objective": str(payload.get("objective") or "").strip(),
        "population": str(payload.get("population") or "").strip(),
        "time_scope": str(payload.get("time_scope") or "").strip(),
        "metric_definitions": metric_defs,
        "semantic_bindings": bindings,
        "assumptions": assumptions,
    }


def _normalise_steps(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not (3 <= len(steps) <= 5):
        raise LLMFormatError("workflow.steps must be a list of 3 to 5 high-level stages.")
    normalised = []
    outputs: set[str] = set()
    source_tables = {"orders", "customers", "order_items", "order_payments", "products"}
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise LLMFormatError(f"workflow step {i} is not an object.")
        operations = step.get("operations") or []
        if not isinstance(operations, list):
            raise LLMFormatError(f"workflow step {i} operations must be a list.")
        clean_ops = []
        for op in operations:
            if not isinstance(op, dict) or op.get("tool") not in LIVE_TOOL_NAMES:
                raise LLMFormatError(f"workflow step {i} contains an unsupported tool: {op!r}")
            args = op.get("args")
            if not isinstance(args, dict):
                raise LLMFormatError(f"workflow step {i} operation args must be an object.")
            output = args.get("output")
            if output is not None:
                if not isinstance(output, str) or not output:
                    raise LLMFormatError(f"workflow step {i} has an invalid output table name.")
                if output in source_tables or output in outputs:
                    raise LLMFormatError(f"output table {output!r} is duplicated or shadows a source table.")
                outputs.add(output)
            clean_ops.append({"tool": op["tool"], "args": args})
        normalised.append(
            {
                "title": str(step.get("title") or f"Step {i}"),
                "goal": str(step.get("goal") or ""),
                "understanding": str(step.get("understanding") or ""),
                "target_data": [str(x) for x in (step.get("target_data") or [])][:4],
                "rationale": str(step.get("rationale") or ""),
                "operations": clean_ops,
                "dependency_chain": [],
            }
        )
    return normalised


def dry_run_steps(steps: list[dict[str, Any]], dataset: OlistDataset) -> list[str]:
    """Execute the plan on copies of source tables to validate it before a run."""
    tables = {name: dataset.table(name).copy(deep=False) for name in ("orders", "customers", "order_items", "order_payments", "products")}
    metrics: dict[str, Any] = {}
    issues: list[str] = []
    for index, step in enumerate(steps, start=1):
        for op_index, op in enumerate(step["operations"], start=1):
            try:
                result = execute_tool(op["tool"], op["args"], tables, dataset, metrics)
            except ToolError as exc:
                issues.append(f"step {index} operation {op_index}: {exc.message}")
                continue
            tables.update(result.updates)
            if result.metric:
                metrics[result.metric.name] = result.metric
    return issues


def infer_reference_kind(task: str) -> tuple[str, str | None]:
    low = task.lower()
    if "repeat" in low or "purchase rate" in low or ("customer" in low and "rate" in low):
        return "live_repeat_purchase", "repeat_purchase"
    if "categor" in low or ("sales" in low and ("month" in low or "product" in low)):
        return "live_category_sales", "category_sales"
    return "live_task", None


_DESTRUCTIVE_TASK_WORDING = re.compile(
    r"\b(?:delete|deletes|deleted|deleting|drop|drops|dropped|dropping|erase|purge|truncate)\w*\b",
    re.IGNORECASE,
)


def _destructive_task_hint(task: str) -> str:
    if not _DESTRUCTIVE_TASK_WORDING.search(task):
        return ""
    return (
        " Destructive cleanup is intentionally unavailable to the Live Agent's read-only toolset. "
        "The prepared Case C scenario runs the equivalent temporal customer cleanup with a controlled "
        "sandbox DELETE and an approval gate: New run → Customer cleanup → Scripted."
    )


def live_plan(task: str, dataset: OlistDataset, client: LLMClient) -> PlanDraft:
    if not client.available:
        raise LiveUnavailable(
            "Live Agent is unavailable: no LLM API key is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env."
        )
    context = {
        "task": task,
        "dataset": "Brazilian E-Commerce Public Dataset by Olist (local CSV copy)",
        "schema": schema_context(dataset),
        "tools": TOOL_SPECS,
        "definitions": {
            "included_status_example": "delivered",
            "grain_note": "orders.customer_id is one customer record per order; customers.customer_unique_id links persistent customers. (order_id, payment_sequential) identifies a payment record; order_items.price excludes freight.",
        },
    }
    user = (
        "Create a high-level plan for this data task.\n\n"
        + json.dumps(context, indent=2)
        + "\n\nReturn the JSON object described in the system prompt. Use 3-5 broad "
        "stages, group related filters/joins/groupings/metrics inside the relevant "
        "stage, and do not create one step per tool call. In the first stage, interpret "
        "only the user's request; introduce tables, columns, joins, and concrete "
        "operations only in the stages that use them. Be explicit about metric "
        "definition and grain."
    )
    payload = client.complete_json(PLAN_SYSTEM, user, max_tokens=10000)
    if not payload.get("supported", True):
        reason = payload.get("unsupported_reason") or "The model declined this task."
        raise LLMFormatError(f"Live agent declined the task: {reason}{_destructive_task_hint(task)}")
    task_spec = _normalise_task_spec(payload.get("task_spec") or {})
    if not task_spec["objective"]:
        raise LLMFormatError("The live plan has no explicit objective.")
    workflow = payload.get("workflow") or {}
    steps = _normalise_steps(workflow.get("steps"))
    issues = dry_run_steps(steps, dataset)
    if issues:
        raise LLMFormatError("The live plan failed controller validation: " + "; ".join(issues[:4]))
    scenario_id, reference_kind = infer_reference_kind(task)
    planner_note = str(workflow.get("planner_note") or "")
    return PlanDraft(
        scenario_id=scenario_id,
        reference_kind=reference_kind,
        task_spec=task_spec,
        steps=steps,
        planner_label=f"Live agent · {client.provider}/{client.model} · model-generated plan",
        notes=[planner_note] if planner_note else [],
    )


REVISION_SYSTEM = """You are the revision module of Data Agent Debugger. You receive an explicit TaskSpec, one selected
workflow step with recorded evidence, and natural-language feedback. Investigate only with permitted read-only tools,
then propose a structured revision. Do not reveal private reasoning.
Return one JSON object only, no markdown fences.

Output schema:
{
  "explanation": "short conversational explanation of what you checked and what you propose",
  "investigations": [{"tool": "inspect_schema|inspect_data_dictionary|profile_column|sample_records", "args": {}}],
  "semantic_bindings": [{"concept": "...", "source_table": "...", "column": "...", "note": "..."}],
  "metric_definitions": [{"name": "...", "definition": "...", "unit": "percent|currency|number"}],
  "assumptions": ["..."],
  "step_updates": [
    {
      "step_id": "existing step id",
      "understanding": "optional replacement; same concise, stage-appropriate style",
      "goal": "optional replacement",
      "target_data": ["optional replacement"],
      "rationale": "optional replacement",
      "operations": [{"tool": "...", "args": {}}]
    }
  ]
}

Rules:
- Use only the supplied tools and exact existing step IDs.
- If operations are supplied for a step, they replace that step's operations entirely.
- When replacing an understanding, explain the corrected purpose of that stage in
  relation to the user's goal. Distinguish the user's requirement from the agent's
  assumption, and name tables, columns, joins, or filters only when that stage uses
  them. If that step is the first interpretation stage, restate only the corrected
  request, metric, scope, and assumptions. Keep it to 1-3 sentences.
- Output table names must not shadow source tables or tables already present in the
  checkpoint. Reuse the selected step's current output names when the operation keeps
  the same role. If you rename an output, include step_updates that update every
  downstream reference in this proposal; never leave a dangling table reference.
- If you revise a report operation, it may reference only results that exist. Use
  exact compute_metric names in metric_names, and pass an output table in `table` for
  a table-shaped result. A grouped table is not a metric.
- If you revise a metric definition, keep the metric name, include the revised
  definition under metric_definitions, and update the definition used by later
  compute_metric/report operations, including a step_update when needed. The
  controller may propagate the revised definition, but the proposal should be
  internally consistent on its own.
- You may return investigations on the first turn. The controller executes them and calls you again with results.
- The final answer must contain no investigations.
- If no revision is justified, return an explanation and empty semantic/plan changes.
"""


def _brief_evidence(run, step) -> list[dict[str, Any]]:
    evidence = []
    for op in step.operations:
        for ev in op.evidence:
            evidence.append(
                {
                    "operation": op.tool,
                    "status": ev.status,
                    "label": ev.label,
                    "summary": ev.summary,
                    "facts": [f.model_dump() for f in ev.facts],
                    "details": ev.details,
                }
            )
    return evidence[:8]


def _run_investigations(
    requests: Any, tables: dict, metrics: dict, dataset: OlistDataset
) -> tuple[list[Investigation], list[dict[str, Any]]]:
    if not isinstance(requests, list):
        return [], []
    investigations: list[Investigation] = []
    payloads: list[dict[str, Any]] = []
    for item in requests[:3]:
        if not isinstance(item, dict) or item.get("tool") not in READ_ONLY_TOOLS:
            raise LLMFormatError("Live revision requested an unsupported investigation tool.")
        tool = item["tool"]
        args = item.get("args") or {}
        if not isinstance(args, dict):
            raise LLMFormatError("Investigation args must be an object.")
        try:
            result = execute_tool(tool, args, tables, dataset, metrics)
        except ToolError as exc:
            raise LLMFormatError(f"Investigation {tool} failed: {exc.message}") from exc
        facts = []
        summary = result.summary
        for ev in result.evidence:
            summary = ev.summary
            facts.extend(ev.facts)
        inv = Investigation(tool=tool, args=args, summary=summary, facts=facts[:6])
        investigations.append(inv)
        payloads.append(
            {
                "tool": tool,
                "args": args,
                "summary": summary,
                "facts": [f.model_dump() for f in facts[:6]],
            }
        )
    return investigations, payloads


def _normalise_bindings(items: Any) -> list[SemanticBinding]:
    if not isinstance(items, list):
        raise LLMFormatError("semantic_bindings must be a list.")
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get("concept") or not item.get("source_table") or not item.get("column"):
            raise LLMFormatError("Each proposed binding needs concept, source_table, and column.")
        out.append(
            SemanticBinding(
                concept=str(item["concept"]),
                source_table=str(item["source_table"]),
                column=str(item["column"]),
                status="revised",
                note=str(item.get("note", "")),
            )
        )
    return out


def _normalise_metrics(items: Any) -> list[MetricDefinition]:
    if not isinstance(items, list):
        raise LLMFormatError("metric_definitions must be a list.")
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get("name") or not item.get("definition"):
            raise LLMFormatError("Each proposed metric needs name and definition.")
        unit = item.get("unit", "number")
        if unit not in {"percent", "currency", "number"}:
            unit = "number"
        out.append(MetricDefinition(name=str(item["name"]), definition=str(item["definition"]), unit=unit, status="revised"))
    return out


def _normalise_step_updates(items: Any, run) -> list[StepUpdate]:
    if not isinstance(items, list):
        raise LLMFormatError("step_updates must be a list.")
    valid_ids = {s.id for s in run.steps}
    out = []
    for item in items:
        if not isinstance(item, dict) or item.get("step_id") not in valid_ids:
            raise LLMFormatError("step_updates contains an unknown step_id.")
        operations = item.get("operations")
        if operations is not None:
            if not isinstance(operations, list):
                raise LLMFormatError("Step update operations must be a list.")
            clean = []
            for op in operations:
                if not isinstance(op, dict) or op.get("tool") not in LIVE_TOOL_NAMES:
                    raise LLMFormatError(f"Step update contains an unsupported tool: {op!r}")
                if not isinstance(op.get("args"), dict):
                    raise LLMFormatError("Operation args must be an object.")
                clean.append({"tool": op["tool"], "args": op["args"]})
            operations = clean
        out.append(
            StepUpdate(
                step_id=item["step_id"],
                title=item.get("title"),
                goal=item.get("goal"),
                understanding=item.get("understanding"),
                target_data=[str(x) for x in item["target_data"]][:4] if isinstance(item.get("target_data"), list) else None,
                rationale=item.get("rationale"),
                operations=operations,
            )
        )
    return out


def live_revision(run, step, user_text: str, dataset: OlistDataset, tables: dict, client: LLMClient) -> dict[str, Any]:
    if not client.available:
        raise LiveUnavailable(
            "Live Agent is unavailable: no LLM API key is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env."
        )
    context = {
        "task": run.task,
        "task_spec": run.task_specs[-1].model_dump(mode="json"),
        "workflow": [
            {
                "id": s.id,
                "index": s.index + 1,
                "title": s.title,
                "status": "completed" if s.status == "completed" else "pending",
                "understanding": s.understanding,
                "target_data": s.target_data,
                "operations": [
                    {"tool": op.tool, "args": op.args, "status": op.status}
                    for op in s.operations
                ],
            }
            for s in run.steps
        ],
        "selected_step": {
            "id": step.id,
            "title": step.title,
            "goal": step.goal,
            "understanding": step.understanding,
            "target_data": step.target_data,
            "status": step.status,
            "operations": [
                {"tool": op.tool, "args": op.args, "status": op.status, "summary": op.summary}
                for op in step.operations
            ],
            "evidence": _brief_evidence(run, step),
        },
        "available_tables": {
            name: {"columns": list(tables[name].columns), "rows": int(len(tables[name]))}
            for name in tables
        },
        "source_schema": schema_context(dataset),
        "tools": [t for t in TOOL_SPECS],
    }
    user = (
        "The developer gave this feedback about the selected step:\n"
        + json.dumps(user_text)
        + "\n\nContext:\n"
        + json.dumps(context, indent=2)
        + "\n\nReturn the revision JSON. You may request up to three read-only investigations on this turn."
    )
    payload = client.complete_json(REVISION_SYSTEM, user, max_tokens=8000)
    investigations, investigation_payloads = _run_investigations(
        payload.get("investigations"), tables, run.metrics, dataset
    )
    if investigation_payloads:
        followuser = (
            "Investigation results from the controller:\n"
            + json.dumps(investigation_payloads, indent=2)
            + "\n\nOriginal feedback:\n"
            + json.dumps(user_text)
            + "\n\nSelected step context:\n"
            + json.dumps(context, indent=2)
            + "\n\nReturn the final revision JSON now. investigations must be empty."
        )
        payload = client.complete_json(REVISION_SYSTEM, followuser, max_tokens=8000)
        if payload.get("investigations"):
            raise LLMFormatError("Live revision requested a second investigation round; only one round is supported.")

    explanation = str(payload.get("explanation") or "").strip()
    if not explanation:
        raise LLMFormatError("Live revision did not include an explanation.")
    bindings = _normalise_bindings(payload.get("semantic_bindings") or [b.model_dump() for b in run.task_specs[-1].semantic_bindings])
    metrics = _normalise_metrics(payload.get("metric_definitions") or [m.model_dump() for m in run.task_specs[-1].metric_definitions])
    assumptions = payload.get("assumptions")
    if assumptions is not None and not isinstance(assumptions, list):
        raise LLMFormatError("assumptions must be a list.")
    step_updates = _normalise_step_updates(payload.get("step_updates") or [], run)
    return {
        "explanation": explanation,
        "proposed_bindings": bindings,
        "proposed_metrics": metrics,
        "proposed_assumptions": [str(a) for a in (assumptions or [])][:8],
        "step_updates": step_updates,
        "investigations": investigations,
        "changes": [],
        "earliest_affected_step": run.steps.index(step),
        "status": "pending",
    }
