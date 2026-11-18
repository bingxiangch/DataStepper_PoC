from __future__ import annotations

import sqlite3

import pytest

from backend.app.runtime import DebuggerError


def _sandbox_counts(service, run_id):
    path = service._sandbox_path(service._get(run_id))
    assert path
    conn = sqlite3.connect(path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM sandbox_orders").fetchone()[0]
        canceled = conn.execute(
            "SELECT COUNT(*) FROM sandbox_orders WHERE order_status = 'canceled'"
        ).fetchone()[0]
        return total, canceled
    finally:
        conn.close()


def _reach_delete_approval(service):
    view = service.create_run(
        "Delete canceled test orders from the disposable sandbox and verify the remaining rows.",
        "scripted",
        "sandbox",
    )
    run_id = view["id"]
    service.step_over(run_id)  # understand
    service.step_over(run_id)  # inspect sandbox
    service.step_over(run_id)  # delete operation pauses for approval
    view = service.get_view(run_id)
    assert view["status"] == "awaiting_approval"
    assert view["approval"]["tool"] == "sandbox_delete_rows"
    return run_id, view


def test_approval_gate_blocks_all_execution_shortcuts(service):
    run_id, view = _reach_delete_approval(service)
    assert _sandbox_counts(service, run_id) == (99441, 625)
    facts = {fact["label"]: fact["value"] for fact in view["approval"]["facts"]}
    assert facts["Affected rows"] == "625"
    assert facts["Rows before"] == "99,441"
    assert "No rows have been changed" in view["approval"]["label"]

    assert view["capabilities"]["continue"] is False
    assert view["capabilities"]["step_over"] is False
    assert view["capabilities"]["step_out"] is False
    assert view["capabilities"]["approve"] is True

    with pytest.raises(DebuggerError) as caught:
        service.continue_run(run_id)
    assert caught.value.code == "approval_pending"
    with pytest.raises(DebuggerError) as caught:
        service.step_over(run_id)
    assert caught.value.code == "approval_pending"
    with pytest.raises(DebuggerError) as caught:
        service.step_out(run_id)
    assert caught.value.code == "invalid_state"
    with pytest.raises(DebuggerError) as caught:
        service.step_into(run_id)
    assert caught.value.code == "approval_pending"

    # No execution path changed the disposable database yet.
    assert _sandbox_counts(service, run_id) == (99441, 625)


def test_approval_executes_exactly_once_and_records_provenance(service):
    run_id, _ = _reach_delete_approval(service)
    view = service.approve(run_id)
    assert view["approval"] is None
    assert view["approval_history"][-1]["status"] == "approved"
    assert _sandbox_counts(service, run_id) == (98816, 0)
    assert view["steps"][2]["operations"][0]["status"] == "completed"
    assert view["capabilities"]["approve"] is False

    with pytest.raises(DebuggerError):
        service.approve(run_id)
    assert _sandbox_counts(service, run_id) == (98816, 0)

    view = service.continue_run(run_id)
    import time

    for _ in range(100):
        time.sleep(0.03)
        view = service.get_view(run_id)
        if view["status"] != "running":
            break
    assert view["status"] == "completed"
    assert view["metrics"]["sandbox_rows"]["formatted"] == "98,816"
    assert any(event["type"] == "approval_granted" for event in service.events.tail(run_id, 30))


def test_rejection_skips_without_writing_and_final_report_uses_pre_delete_count(service):
    run_id, _ = _reach_delete_approval(service)
    view = service.reject_approval(run_id, "Not authorized in this demo")
    assert view["approval"] is None
    assert view["approval_history"][-1]["status"] == "rejected"
    assert view["steps"][2]["status"] == "skipped"
    assert view["steps"][2]["operations"][0]["status"] == "rejected"
    assert _sandbox_counts(service, run_id) == (99441, 625)
    assert view["capabilities"]["continue"] is True

    view = service.continue_run(run_id)
    import time

    for _ in range(100):
        time.sleep(0.03)
        view = service.get_view(run_id)
        if view["status"] != "running":
            break
    assert view["status"] == "completed"
    assert view["steps"][2]["status"] == "skipped"
    assert view["steps"][2]["is_skipped"] is True
    assert view["metrics"]["sandbox_rows"]["formatted"] == "99,441"
    assert view["report"]["summary"].startswith(
        "The destructive operation was rejected at the approval gate; no rows were changed."
    )
    assert any("Not authorized in this demo" in note for note in view["report"]["notes"])


def test_sandbox_feedback_has_no_prepared_plan_revision(service):
    run_id, _ = _reach_delete_approval(service)
    with pytest.raises(DebuggerError):
        service.feedback(run_id, "step_3", "Please do something different.")
    # Pending approval is a blocking state; this is an explicit controller error.
