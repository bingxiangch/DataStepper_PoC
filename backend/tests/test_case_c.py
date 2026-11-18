"""Case C: prospective intervention on a temporal DELETE."""

from __future__ import annotations

import sqlite3

from backend.app.runtime import DebuggerError

TASK = (
    "Clean up our customer table: delete customers who have not placed an order "
    "in the last 12 months."
)
FEEDBACK = (
    "Evaluate inactivity relative to the latest order date in the historical dataset, "
    "and archive the rows instead of deleting them."
)


def _customer_counts(service, run_id: str) -> tuple[int, int]:
    path = service._sandbox_path(service._get(run_id))
    assert path
    conn = sqlite3.connect(path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM sandbox_customers").fetchone()[0]
        archived = conn.execute(
            "SELECT COUNT(*) FROM sandbox_customers WHERE archived = 1"
        ).fetchone()[0]
        return total, archived
    finally:
        conn.close()


def _reach_gate(service) -> tuple[str, dict]:
    view = service.create_run(TASK, "scripted", "sandbox")
    run_id = view["id"]
    assert view["scenario_id"] == "customer_cleanup"

    service.step_over(run_id)  # understand
    service.step_over(run_id)  # read-only preview
    view = service.step_over(run_id)  # DELETE pauses at the gate
    assert view["status"] == "awaiting_approval"
    assert view["approval"]["tool"] == "sandbox_delete_inactive_customers"
    assert view["approval"]["mandatory"] is True
    assert view["approval"]["matched_breakpoint"]["operation_types"] == ["delete", "drop"]
    return run_id, view


def test_case_c_preview_and_gate_expose_temporal_assumptions(service):
    view = service.create_run(TASK, "scripted", "sandbox")
    run_id = view["id"]
    assert view["breakpoints"][0]["active"] is True
    assert view["task_spec"]["time_scope"] == "The last 12 months relative to the current date at run time."

    service.step_over(run_id)
    view = service.step_over(run_id)
    preview = next(
        evidence
        for operation in view["steps"][1]["operations"]
        for evidence in operation["evidence"]
        if evidence["kind"] == "sandbox_customer_preview"
    )
    details = preview["details"]
    assert details["customers_total"] == 99441
    assert details["matching_customers"] == 99441
    assert details["never_ordered"] == 0
    assert details["dataset_first_order"].startswith("2016-09-04")
    assert details["dataset_last_order"].startswith("2018-10-17")
    assert details["declared_reference_date"].startswith("Current date")
    assert "no rows" in preview["summary"].lower()
    assert view["metrics"]["inactive_customers"]["formatted"] == "99,441"

    view = service.step_over(run_id)
    assert view["status"] == "awaiting_approval"
    facts = {fact["label"]: fact["value"] for fact in view["approval"]["facts"]}
    assert facts["Target"] == "sandbox_customers (disposable SQLite)"
    assert facts["Matching rows"] == "99,441"
    assert facts["Customers before"] == "99,441"
    assert facts["Never ordered"] == "0 (included)"
    assert facts["Order range"] == "2016-09-04 → 2018-10-17"
    assert "No rows have been changed" in view["approval"]["label"]
    assert _customer_counts(service, run_id) == (99441, 0)


def test_case_c_feedback_at_gate_revises_to_dataset_latest_and_archive(service):
    run_id, view = _reach_gate(service)
    step_id = view["approval"]["step_id"]

    view = service.feedback(run_id, step_id, FEEDBACK)
    proposal = view["revisions"][-1]
    assert proposal["status"] == "pending"
    assert proposal["execution_changed"] is True
    assert proposal["earliest_affected_step"] == 1
    assert "99,441" in proposal["explanation"]
    assert "30,096" in proposal["explanation"]
    assert "2018-10-17 17:30:18" in proposal["explanation"]

    changes = {(change["kind"], change["description"]) for change in proposal["changes"]}
    assert ("binding", "The temporal reference moves from the current date to the dataset's latest order.") in changes
    assert ("binding", "The cleanup action moves from permanent row deletion to an archival flag.") in changes
    assert ("plan", "The preview, write, and verification operations are rewritten for the revision.") in changes

    updates = {update["step_id"]: update for update in proposal["step_updates"]}
    assert updates["step_2"]["operations"][0]["args"]["reference"] == "dataset_latest"
    assert updates["step_3"]["title"] == "Archive Inactive Customers"
    assert updates["step_3"]["operations"][0]["tool"] == "sandbox_archive_inactive_customers"
    assert updates["step_4"]["operations"][0]["args"]["reference"] == "dataset_latest"
    assert proposal["proposed_metrics"][0]["status"] == "confirmed"

    # Feedback at a breakpoint gate does not resolve the gate by itself.
    current = service.get_view(run_id)
    assert current["status"] == "awaiting_approval"
    assert current["approval"]["status"] == "pending"
    assert _customer_counts(service, run_id) == (99441, 0)


def test_case_c_accept_and_replay_archives_without_removing_source_rows(service):
    run_id, view = _reach_gate(service)
    view = service.feedback(run_id, view["approval"]["step_id"], FEEDBACK)
    revision_id = view["revisions"][-1]["id"]

    view = service.accept_revision(run_id, revision_id)
    assert view["accepted_revision_id"] == revision_id
    # The parent stays at its gate; replay branches from the earlier checkpoint.
    assert view["status"] == "awaiting_approval"

    result = service.replay(run_id, revision_id)
    child = result["run"]
    comparison = result["comparison"]
    assert child["status"] == "completed"
    assert child["error"] is None
    assert child["metrics"]["inactive_customers"]["formatted"] == "30,096"
    assert child["metrics"]["customers_archived"]["formatted"] == "30,096"
    assert child["metrics"]["customers_remaining"]["formatted"] == "99,441"
    assert comparison["affected_from_step"] == 2
    assert comparison["parent_metrics"]["inactive_customers"]["formatted"] == "99,441"
    assert comparison["child_metrics"]["inactive_customers"]["formatted"] == "30,096"
    assert comparison["parent_status"] == "awaiting_approval"
    assert comparison["child_status"] == "completed"
    plan_diff = {entry["step_number"]: entry for entry in comparison["plan_diff"]}
    assert plan_diff[3]["old_operations"] == ["sandbox_delete_inactive_customers"]
    assert plan_diff[3]["new_operations"] == ["sandbox_archive_inactive_customers"]

    audit = next(
        evidence
        for operation in child["steps"][3]["operations"]
        for evidence in operation["evidence"]
        if evidence["kind"] == "sandbox_audit"
    )
    facts = {fact["label"]: fact["value"] for fact in audit["facts"]}
    assert facts["Source customer rows"] == "99,441"
    assert facts["Sandbox customer rows"] == "99,441"
    assert facts["Rows removed"] == "0"
    assert facts["Archived"] == "30,096"
    assert facts["Active (not archived)"] == "69,345"
    assert facts["Declared reference"] == "Dataset latest order (2018-10-17 17:30:18)"
    assert facts["Cutoff"] == "2017-10-17 17:30:18"

    # The original run and its disposable copy are unchanged.
    parent = service.get_view(run_id)
    assert parent["status"] == "awaiting_approval"
    assert parent["metrics"]["inactive_customers"]["formatted"] == "99,441"
    assert _customer_counts(service, run_id) == (99441, 0)
    assert _customer_counts(service, child["id"]) == (99441, 30096)

    # The same revision cannot be replayed twice into divergent children.
    again = service.replay(run_id, revision_id)
    assert again["run"]["id"] == child["id"]
    assert service.get_view(run_id)["child_run_ids"] == [child["id"]]
    with pytest_raises_code("invalid_revision_state"):
        service.accept_revision(run_id, revision_id)


def test_case_c_approved_delete_executes_once_and_audits_the_loss(service):
    run_id, _ = _reach_gate(service)
    view = service.approve(run_id)
    assert view["steps"][2]["operations"][0]["status"] == "completed"
    assert _customer_counts(service, run_id) == (0, 0)

    view = service.step_over(run_id)  # verify and report
    assert view["status"] == "completed"
    assert view["metrics"]["customers_remaining"]["formatted"] == "0"
    audit = next(
        evidence
        for operation in view["steps"][3]["operations"]
        for evidence in operation["evidence"]
        if evidence["kind"] == "sandbox_audit"
    )
    facts = {fact["label"]: fact["value"] for fact in audit["facts"]}
    assert facts["Source customer rows"] == "99,441"
    assert facts["Sandbox customer rows"] == "0"
    assert facts["Rows removed"] == "99,441"
    assert "99,441 removed from the disposable copy" in audit["summary"]


def test_case_c_gate_rejection_changes_nothing(service):
    run_id, _ = _reach_gate(service)
    view = service.reject_approval(run_id, "Historical data must not be deleted.")
    assert view["approval"] is None
    assert view["approval_history"][-1]["status"] == "rejected"
    assert view["steps"][2]["status"] == "skipped"
    assert view["steps"][2]["operations"][0]["status"] == "rejected"
    assert _customer_counts(service, run_id) == (99441, 0)

    view = service.continue_run(run_id)
    import time

    for _ in range(200):
        view = service.get_view(run_id)
        if view["status"] != "running":
            break
        time.sleep(0.03)
    assert view["status"] == "completed"
    assert view["steps"][2]["is_skipped"] is True
    assert view["metrics"]["customers_remaining"]["formatted"] == "99,441"
    assert _customer_counts(service, run_id) == (99441, 0)


class pytest_raises_code:
    """Tiny context manager mirroring pytest.raises(...).value.code."""

    def __init__(self, code: str):
        self.code = code
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(f"expected DebuggerError code {self.code!r}")
        assert issubclass(exc_type, DebuggerError)
        assert exc.code == self.code
        self.value = exc
        return True
