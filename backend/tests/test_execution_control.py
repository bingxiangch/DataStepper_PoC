from __future__ import annotations

import json

import pytest

from backend.app.runtime import DebuggerError


def _artifact_files(service):
    return {path.name for path in service.store.root.glob("art_*.parquet")}


def test_step_into_enters_without_executing_then_executes_one_operation(service):
    view = service.create_run("What is our customer repeat purchase rate?", "scripted")
    run_id = view["id"]
    service.step_over(run_id)  # interpretation-only step 1
    view = service.get_view(run_id)
    assert view["cursor"]["step_index"] == 1

    tables_before = dict(service.runs[run_id].tables)
    files_before = _artifact_files(service)
    event_before = view["event_count"]

    view = service.step_into(run_id)
    assert view["status"] == "paused_before_operation"
    assert view["cursor"]["inside"] is True
    assert [op["status"] for op in view["steps"][1]["operations"]] == ["pending", "pending", "pending"]
    assert all(item["status"] == "PREVIEW" for item in view["steps"][1]["evidence"])
    assert view["steps"][1]["preview_evidence"]
    assert all(item["status"] == "PREVIEW" for item in view["steps"][1]["preview_evidence"])
    assert service.runs[run_id].tables == tables_before
    assert _artifact_files(service) == files_before
    assert view["event_count"] == event_before + 1  # entering records a command, executes nothing

    # Rendering a view again must not persist preview evidence/artifacts.
    again = service.get_view(run_id)
    assert all(item["status"] == "PREVIEW" for item in again["steps"][1]["evidence"])
    assert _artifact_files(service) == files_before

    view = service.step_into(run_id)
    statuses = [op["status"] for op in view["steps"][1]["operations"]]
    assert statuses == ["completed", "pending", "pending"]
    assert view["status"] == "paused_before_operation"
    assert view["cursor"]["op_index"] == 1
    observed = [item for item in view["steps"][1]["evidence"] if item["status"] == "OBSERVED"]
    previews = [item for item in view["steps"][1]["evidence"] if item["status"] == "PREVIEW"]
    assert len(observed) == 1
    assert previews
    assert observed[0]["source_artifact_ids"] == ["src:orders"]  # source input, no produced table


def test_step_into_skips_interpretation_only_step_and_enters_first_operation(service):
    view = service.create_run("What is our customer repeat purchase rate?", "scripted")
    run_id = view["id"]
    assert view["steps"][0]["operations"] == []

    view = service.step_into(run_id)
    assert view["steps"][0]["status"] == "completed"
    assert view["status"] == "paused_before_step"
    assert view["cursor"]["step_index"] == 1

    view = service.step_into(run_id)
    assert view["status"] == "paused_before_operation"
    assert view["steps"][1]["status"] == "inside"
    assert all(op["status"] == "pending" for op in view["steps"][1]["operations"])


def test_step_over_executes_whole_step_and_step_out_requires_inside(service):
    view = service.create_run("What is our customer repeat purchase rate?", "scripted")
    run_id = view["id"]
    view = service.step_over(run_id)  # step 1 has no operations
    view = service.step_into(run_id)  # enter step 2, executes nothing
    assert view["cursor"]["inside"] is True
    view = service.step_out(run_id)
    assert view["status"] == "paused_before_step"
    assert view["cursor"]["step_index"] == 2
    assert all(op["status"] == "completed" for op in view["steps"][1]["operations"])
    assert all(ev["status"] == "OBSERVED" for ev in view["steps"][1]["evidence"])

    with pytest.raises(DebuggerError) as caught:
        service.step_out(run_id)
    assert caught.value.code == "invalid_state"

    view = service.step_over(run_id)
    assert view["steps"][2]["status"] == "completed"
    assert view["steps"][2]["operations"][0]["tool"] == "filter_rows"
    assert view["steps"][2]["operations"][1]["output_artifact_ids"]


def test_completed_and_historical_steps_are_read_only(service):
    view = service.create_run("What is our customer repeat purchase rate?", "scripted")
    run_id = view["id"]
    from backend.tests.conftest import complete_run

    view = complete_run(service, run_id)
    before = service.get_step_view(run_id, view["steps"][2]["id"])
    metric_before = view["metrics"]["repeat_purchase_rate"]["value"]
    artifacts_before = dict(service.runs[run_id].tables)

    # Repeated historical inspection returns the same evidence, not a re-execution.
    after = service.get_step_view(run_id, view["steps"][2]["id"])
    assert before["evidence"] == after["evidence"]
    assert before["operations"] == after["operations"]
    assert service.runs[run_id].tables == artifacts_before

    with pytest.raises(DebuggerError):
        service.step_over(run_id)
    with pytest.raises(DebuggerError):
        service.step_into(run_id)
    with pytest.raises(DebuggerError):
        service.step_out(run_id)

    # Feedback is allowed, but it does not mutate the completed execution.
    view = service.feedback(
        run_id,
        view["steps"][2]["id"],
        "customer_id is a per-order record key; use the persistent customer identity.",
    )
    assert view["metrics"]["repeat_purchase_rate"]["value"] == metric_before
    assert service.runs[run_id].tables == artifacts_before
    proposal = view["revisions"][-1]
    assert proposal["status"] == "pending"
    assert proposal["execution_changed"] is True


def test_invalid_transitions_and_idempotent_commands(service):
    view = service.create_run("What is our customer repeat purchase rate?", "scripted")
    run_id = view["id"]
    with pytest.raises(DebuggerError):
        service.pause(run_id)
    with pytest.raises(DebuggerError):
        service.step_out(run_id)

    command = "fixed-command-1"
    first = service.step_over(run_id, command)
    event_count = first["event_count"]
    second = service.step_over(run_id, command)
    assert second["idempotent_replay"] is True
    assert second["cursor"] == first["cursor"]
    assert second["event_count"] == event_count


def test_continue_runs_in_background_then_pause_stops_at_safe_boundary(service, monkeypatch):
    monkeypatch.setenv("DEBUGGER_OPERATION_DELAY", "0.2")
    view = service.create_run("What is our customer repeat purchase rate?", "scripted")
    run_id = view["id"]
    service.step_over(run_id)  # skip interpretation-only step
    service.continue_run(run_id)
    # Pause is delivered through a separate event, so it must not wait for the
    # worker's data-operation lock to be released before taking effect.
    service.pause(run_id)
    view = service.get_view(run_id)
    assert view["status"] in {"paused_before_step", "paused_before_operation"}
    assert not service._get(run_id).pause_requested
    assert view["event_count"] > 0
    assert any(item["type"] in {"pause_requested", "paused"} for item in view["events_tail"])

    # The paused run is inspectable and executable again at the boundary.
    assert view["capabilities"]["step_into"] is True
    assert view["capabilities"]["continue"] is True
