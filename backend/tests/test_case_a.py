from __future__ import annotations

import json

from backend.tests.conftest import complete_run


def _group_evidence(view):
    for item in view["steps"][2]["evidence"]:
        if item["kind"] == "groupby" and item["status"] == "OBSERVED":
            return item["details"]
    raise AssertionError("no observed groupby evidence")


def test_case_a_wrong_identity_is_executable_then_replay_matches_reference(service):
    view = service.create_run("What is our customer repeat purchase rate?", "scripted")
    run_id = view["id"]
    assert view["scenario_id"] == "repeat_purchase"

    view = complete_run(service, run_id)
    original_metric = view["metrics"]["repeat_purchase_rate"]
    assert original_metric["value"] == 0.0
    assert original_metric["formatted"] == "0.0000%"

    details = _group_evidence(view)
    assert details["output_groups"] == 96478
    assert details["groups_with_multiple_rows"] == 0

    check = view["reference_check"]
    assert check["agreement"] is False
    assert round(check["reference_value"], 10) == round(2801 / 93358, 10)
    assert check["numerator"] == 2801
    assert check["denominator"] == 93358
    assert check["diagnostics"]["order_record_key_groups"] == 96478
    assert check["diagnostics"]["order_record_key_repeat_groups"] == 0

    parent_artifacts = dict(service.runs[run_id].tables)
    parent_events = view["event_count"]

    view = service.feedback(
        run_id,
        view["steps"][2]["id"],
        "customer_id is not the persistent customer identity. Repeat purchase should group by customer_unique_id.",
    )
    proposal = view["revisions"][-1]
    assert proposal["status"] == "pending"
    assert proposal["execution_changed"] is True
    assert proposal["earliest_affected_step"] == 2
    assert any(change["kind"] == "binding" for change in proposal["changes"])
    binding = proposal["proposed_bindings"][0]
    assert (binding["source_table"], binding["column"]) == ("customers", "customer_unique_id")
    assert service.runs[run_id].tables == parent_artifacts
    assert service.get_view(run_id)["metrics"]["repeat_purchase_rate"]["value"] == 0.0

    view = service.accept_revision(run_id, proposal["id"])
    assert view["accepted_revision_id"] == proposal["id"]
    assert view["status"] == "completed"  # parent stays immutable and needs a replay
    assert view["capabilities"]["replay"] is True
    assert view["metrics"]["repeat_purchase_rate"]["value"] == 0.0
    assert service.runs[run_id].tables == parent_artifacts
    assert service.runs[run_id].event_count >= parent_events

    result = service.replay(run_id)
    child = result["run"]
    comparison = result["comparison"]
    assert child["status"] == "completed"
    assert child["parent_run_id"] == run_id
    assert child["replay_of_revision_id"] == proposal["id"]
    child_metric = child["metrics"]["repeat_purchase_rate"]
    assert child_metric["value"] == 2801 / 93358
    assert child_metric["formatted"] == "3.0003%"
    assert round(child["reference_check"]["difference"], 12) == 0.0
    assert child["reference_check"]["agreement"] is True
    assert child["capabilities"]["replay"] is False
    assert child["capabilities"]["compare"] is False

    # The original run and its artifacts are untouched.
    parent = service.get_view(run_id)
    assert parent["metrics"]["repeat_purchase_rate"]["value"] == 0.0
    assert service.runs[run_id].tables == parent_artifacts

    assert comparison["affected_from_step"] == 3
    assert comparison["binding_diff"] == [
        {
            "concept": "Customer Identity",
            "old": "orders.customer_id",
            "new": "customers.customer_unique_id",
        }
    ]
    diff = comparison["metric_definitions_diff"][0]
    assert diff["old_formatted"] == "0.0000%"
    assert diff["new_formatted"] == "3.0003%"
    steps = {item["step_number"]: item for item in comparison["plan_diff"]}
    assert steps[3]["old_operations"] == ["filter_rows", "group_and_aggregate"]
    assert steps[3]["new_operations"] == ["filter_rows", "join_tables", "group_and_aggregate"]
    assert [item["step_number"] for item in comparison["invalidated"]] == [3, 4, 5]
    assert comparison["parent_reference_check"]["agreement"] is False
    assert comparison["child_reference_check"]["agreement"] is True

    # Replay is idempotent: asking again returns the existing branch, not a new run.
    again = service.replay(run_id)
    assert again["run"]["id"] == child["id"]
    assert len(service.get_view(run_id)["child_run_ids"]) == 1

    # A child branch cannot be replayed itself.
    assert child["capabilities"]["replay"] is False


def test_case_a_proposal_rejection_leaves_original_untouched(service):
    view = service.create_run("What is our customer repeat purchase rate?", "scripted")
    run_id = view["id"]
    view = complete_run(service, run_id)
    proposal_view = service.feedback(run_id, view["steps"][3]["id"], "Please double check the customer identifier.")
    proposal = proposal_view["revisions"][-1]
    rejected = service.reject_revision(run_id, proposal["id"])
    assert rejected["revisions"][-1]["status"] == "rejected"
    assert rejected["accepted_revision_id"] is None
    assert rejected["capabilities"]["replay"] is False
    assert rejected["metrics"]["repeat_purchase_rate"]["value"] == 0.0
