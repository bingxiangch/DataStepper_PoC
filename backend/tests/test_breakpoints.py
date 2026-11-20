from __future__ import annotations

import pytest

from backend.app.breakpoints import (
    interpret_breakpoint,
    matching_breakpoint,
    matching_data_effect_breakpoint,
)
from backend.app.models import BreakpointCondition, BreakpointRule, Operation, Run
from backend.app.runtime import DebuggerError


def _sandbox_counts(service, run_id):
    import sqlite3

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


def _reach_breakpoint_delete(service, text="Pause before DELETE or DROP operations."):
    view = service.create_run(
        "Delete canceled test orders from the disposable sandbox and verify the remaining rows.",
        "scripted",
        "sandbox",
    )
    run_id = view["id"]
    view = service.add_breakpoint(run_id, text)
    assert len(view["breakpoints"]) == 1
    service.step_over(run_id)  # understand
    service.step_over(run_id)  # inspect sandbox
    service.step_over(run_id)  # DELETE pauses at the breakpoint + mandatory gate
    view = service.get_view(run_id)
    assert view["status"] == "awaiting_approval"
    assert view["approval"]["tool"] == "sandbox_delete_rows"
    return run_id, view


def test_breakpoint_parser_translates_delete_and_drop():
    result = interpret_breakpoint("Pause before the agent performs DELETE or DROP operations.")
    assert result.supported is True
    assert result.rule is not None
    assert result.rule.operation_types == ["delete", "drop"]
    assert result.rule.phase == "before"
    assert result.rule.label == "Pause before DELETE or DROP operations."
    assert "DELETE or DROP" in result.message

    delete_only = interpret_breakpoint("Stop before deleting sandbox rows.")
    assert delete_only.supported is True
    assert delete_only.rule is not None
    assert delete_only.rule.operation_types == ["delete"]

    drop_only = interpret_breakpoint("Pause before the agent drops a table.")
    assert drop_only.supported is True
    assert drop_only.rule is not None
    assert drop_only.rule.operation_types == ["drop"]


def test_breakpoint_parser_translates_cancelled_order_exclusion():
    text = "Pause if the agent excludes cancelled orders from the analysis."
    result = interpret_breakpoint(text)
    assert result.supported is True
    assert result.rule is not None
    assert result.rule.kind == "filter_excludes"
    assert result.rule.operation_types == []
    assert result.rule.condition is not None
    assert result.rule.condition.column == "order_status"
    assert result.rule.condition.value == "canceled"
    assert result.rule.label == text

    filtered_out = interpret_breakpoint("Pause when the agent filters out canceled order rows.")
    assert filtered_out.supported is True
    assert filtered_out.rule is not None
    assert filtered_out.rule.kind == "filter_excludes"


def test_breakpoint_parser_translates_payment_repetition():
    text = "Pause if a payment record is repeated more than 2 times."
    result = interpret_breakpoint(text)
    assert result.supported is True
    assert result.rule is not None
    assert result.rule.kind == "data_effect"
    assert result.rule.operation_types == []
    assert result.rule.condition is not None
    assert result.rule.condition.metric == "max_copies_per_payment_record"
    assert result.rule.condition.threshold == 2
    assert result.rule.label == "Pause when a payment record is repeated more than 2 times."
    assert "payment" in result.message.lower()

    default = interpret_breakpoint("Pause when payment records are duplicated by the join.")
    assert default.supported is True
    assert default.rule is not None
    assert default.rule.kind == "data_effect"
    assert default.rule.condition is not None
    assert default.rule.condition.threshold == 1


@pytest.mark.parametrize(
    "text",
    [
        "Pause after the agent performs DELETE operations.",
        "Pause before the agent performs UPDATE operations.",
        "Pause before the agent executes arbitrary SQL.",
        "Pause if the agent excludes customers from the analysis.",
        "Pause before DELETE operations and exclude cancelled orders.",
        "",
    ],
)
def test_breakpoint_parser_rejects_unsupported_language(text):
    result = interpret_breakpoint(text)
    assert result.supported is False
    assert result.rule is None
    assert result.message


def test_breakpoint_error_names_both_supported_kinds():
    result = interpret_breakpoint("Pause before the agent performs UPDATE operations.")
    assert result.supported is False
    assert "DELETE/DROP" in result.message
    assert "cancelled orders" in result.message


def test_matching_breakpoint_is_deterministic_and_supports_drop():
    run = Run(
        id="run_test",
        task="test",
        mode="scripted",
        dataset="sandbox",
        scenario_id="sandbox_cleanup",
        planner_label="test",
        breakpoints=[
            BreakpointRule(
                id="bp_drop",
                natural_language="Pause before DROP operations.",
                label="Pause before DROP operations.",
                operation_types=["drop"],
            )
        ],
    )
    drop_op = Operation(id="op_1", step_id="step_1", tool="sandbox_drop_table")
    delete_op = Operation(id="op_2", step_id="step_1", tool="sandbox_delete_rows")
    inspect_op = Operation(id="op_3", step_id="step_1", tool="sandbox_inspect")
    assert matching_breakpoint(run, drop_op).id == "bp_drop"
    assert matching_breakpoint(run, delete_op) is None
    assert matching_breakpoint(run, inspect_op) is None

    run.breakpoints[0].active = False
    assert matching_breakpoint(run, drop_op) is None


def test_data_effect_breakpoint_matches_join_preview_threshold():
    run = Run(
        id="run_effect",
        task="test",
        mode="scripted",
        dataset="olist",
        scenario_id="category_sales",
        planner_label="test",
        breakpoints=[
            BreakpointRule(
                id="bp_effect",
                natural_language="Pause if a payment record is repeated.",
                label="Pause when a payment record is repeated.",
                kind="data_effect",
                condition=BreakpointCondition(
                    metric="max_copies_per_payment_record",
                    threshold=2,
                ),
            )
        ],
    )
    join_op = Operation(
        id="op_join",
        step_id="step_3",
        tool="join_tables",
        args={"left": "order_items", "right": "order_payments", "output": "joined"},
    )
    details = {"max_copies_per_payment_record": 3}
    assert matching_data_effect_breakpoint(run, join_op, details).id == "bp_effect"
    assert matching_data_effect_breakpoint(run, join_op, {"max_copies_per_payment_record": 2}) is None
    assert matching_data_effect_breakpoint(run, join_op, {"max_copies_per_payment_record": 1}) is None
    assert matching_data_effect_breakpoint(run, join_op, {}) is None
    non_join = Operation(id="op_filter", step_id="step_3", tool="filter_rows")
    assert matching_data_effect_breakpoint(run, non_join, details) is None

    run.breakpoints[0].active = False
    assert matching_data_effect_breakpoint(run, join_op, details) is None


def _filter_operation(where, tool="filter_rows"):
    return Operation(
        id="op_filter",
        step_id="step_3",
        tool=tool,
        args={"table": "orders", "where": where, "output": "included_orders"},
    )


def test_cancelled_exclusion_breakpoint_matches_filter_predicates():
    run = Run(
        id="run_filter",
        task="test",
        mode="scripted",
        dataset="olist",
        scenario_id="repeat_purchase",
        planner_label="test",
        breakpoints=[
            BreakpointRule(
                id="bp_cancel",
                natural_language="Pause if the agent excludes cancelled orders from the analysis.",
                label="Pause if the agent excludes cancelled orders from the analysis.",
                kind="filter_excludes",
                condition=BreakpointCondition(),
            )
        ],
    )
    assert (
        matching_breakpoint(
            run,
            _filter_operation([{"column": "order_status", "op": "eq", "value": "delivered"}]),
        ).id
        == "bp_cancel"
    )
    assert (
        matching_breakpoint(
            run,
            _filter_operation([{"column": "order_status", "op": "ne", "value": "canceled"}]),
        ).id
        == "bp_cancel"
    )
    assert matching_breakpoint(
        run,
        _filter_operation(
            [{"column": "order_status", "op": "in", "value": ["delivered", "shipped"]}]
        ),
    ) is not None
    assert matching_breakpoint(
        run,
        _filter_operation(
            [{"column": "order_status", "op": "not_in", "value": ["delivered", "canceled"]}]
        ),
    ) is not None

    # Predicates that keep cancelled rows, or that cannot prove exclusion, do not match.
    assert (
        matching_breakpoint(
            run,
            _filter_operation([{"column": "order_status", "op": "eq", "value": "canceled"}]),
        )
        is None
    )
    assert (
        matching_breakpoint(
            run,
            _filter_operation(
                [{"column": "order_status", "op": "in", "value": ["canceled", "delivered"]}]
            ),
        )
        is None
    )
    assert (
        matching_breakpoint(
            run,
            _filter_operation([{"column": "order_status", "op": "ne", "value": "delivered"}]),
        )
        is None
    )
    assert (
        matching_breakpoint(
            run,
            _filter_operation([{"column": "order_status", "op": "contains", "value": "cancel"}]),
        )
        is None
    )
    assert matching_breakpoint(run, _filter_operation([], tool="sandbox_inspect")) is None
    assert (
        matching_breakpoint(
            run,
            _filter_operation([{"column": "customer_id", "op": "eq", "value": "x"}]),
        )
        is None
    )

    run.breakpoints[0].active = False
    assert (
        matching_breakpoint(
            run,
            _filter_operation([{"column": "order_status", "op": "eq", "value": "delivered"}]),
        )
        is None
    )


def _reach_filter_breakpoint(service):
    view = service.create_run(
        "What is our customer repeat purchase rate?",
        "scripted",
        "olist",
    )
    run_id = view["id"]
    view = service.add_breakpoint(run_id, "Pause before DELETE or DROP operations.")
    view = service.add_breakpoint(
        run_id, "Pause if the agent excludes cancelled orders from the analysis."
    )
    assert len(view["breakpoints"]) == 2
    service.step_over(run_id)  # interpretation
    service.step_over(run_id)  # explore data
    service.step_over(run_id)  # step 3 starts; the filter pauses
    view = service.get_view(run_id)
    assert view["status"] == "awaiting_approval"
    assert view["approval"]["tool"] == "filter_rows"
    return run_id, view


def test_two_breakpoints_coexist_and_filter_pause_is_non_destructive(service):
    run_id, view = _reach_filter_breakpoint(service)
    rules = {rule["kind"]: rule for rule in view["breakpoints"]}
    assert set(rules) == {"operation", "filter_excludes"}
    assert rules["filter_excludes"]["condition"] == {
        "column": "order_status",
        "value": "canceled",
    }

    approval = view["approval"]
    assert approval["mandatory"] is False
    assert approval["matched_breakpoint"]["kind"] == "filter_excludes"
    assert approval["matched_breakpoint"]["condition"]["value"] == "canceled"
    assert view["status_label"] == "Paused at breakpoint"
    assert view["capabilities"]["approve"] is True
    assert view["capabilities"]["reject"] is True
    assert view["capabilities"]["continue"] is False

    approved = service.approve(run_id)
    assert approved["approval"] is None
    assert approved["status"] == "paused_before_operation"
    filter_op = approved["steps"][2]["operations"][0]
    assert filter_op["tool"] == "filter_rows"
    assert filter_op["status"] == "completed"


def test_adding_the_same_two_breakpoints_is_idempotent_via_api(api):
    view = api.post(
        "/api/runs",
        json={
            "task": "What is our customer repeat purchase rate?",
            "mode": "scripted",
            "dataset": "olist",
        },
    ).json()
    run_id = view["id"]
    for text in (
        "Pause before DELETE or DROP operations.",
        "Pause if the agent excludes cancelled orders from the analysis.",
    ):
        response = api.post(f"/api/runs/{run_id}/breakpoints", json={"text": text})
        assert response.status_code == 201, response.text
    repeated = api.post(
        f"/api/runs/{run_id}/breakpoints",
        json={"text": "Pause if the agent excludes cancelled orders from the analysis."},
    )
    assert repeated.status_code == 201
    rules = repeated.json()["breakpoints"]
    assert len(rules) == 2
    condition_rules = [rule for rule in rules if rule["kind"] == "filter_excludes"]
    assert len(condition_rules) == 1
    assert condition_rules[0]["condition"] == {
        "column": "order_status",
        "value": "canceled",
    }


def test_breakpoint_activation_and_removal_via_api(api):
    view = api.post(
        "/api/runs",
        json={
            "task": "Delete canceled test orders from the disposable sandbox.",
            "mode": "scripted",
            "dataset": "sandbox",
        },
    ).json()
    run_id = view["id"]

    interpreted = api.post(
        f"/api/runs/{run_id}/breakpoints/interpret",
        json={"text": "Pause before the agent performs DELETE or DROP operations."},
    )
    assert interpreted.status_code == 200
    body = interpreted.json()
    assert body["supported"] is True
    assert body["rule"]["operation_types"] == ["delete", "drop"]

    created = api.post(
        f"/api/runs/{run_id}/breakpoints",
        json={
            "text": "Pause before the agent performs DELETE or DROP operations.",
            "command_id": "api-breakpoint-1",
        },
    )
    assert created.status_code == 201, created.text
    active = created.json()["breakpoints"]
    assert len(active) == 1
    assert active[0]["operation_types"] == ["delete", "drop"]

    # Repeating the same command is idempotent.
    repeated = api.post(
        f"/api/runs/{run_id}/breakpoints",
        json={
            "text": "Pause before the agent performs DELETE or DROP operations.",
            "command_id": "api-breakpoint-1",
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["idempotent_replay"] is True
    assert len(repeated.json()["breakpoints"]) == 1

    removed = api.request(
        "DELETE",
        f"/api/runs/{run_id}/breakpoints/{active[0]['id']}",
        json={"command_id": "api-breakpoint-remove-1"},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["breakpoints"] == []


def test_unsupported_breakpoint_activation_is_rejected(api):
    view = api.post(
        "/api/runs",
        json={
            "task": "Delete canceled test orders from the disposable sandbox.",
            "mode": "scripted",
            "dataset": "sandbox",
        },
    ).json()
    run_id = view["id"]
    response = api.post(
        f"/api/runs/{run_id}/breakpoints",
        json={"text": "Pause after DELETE operations."},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_breakpoint"


def test_breakpoint_pauses_before_delete_and_keeps_mandatory_gate(service):
    run_id, view = _reach_breakpoint_delete(service)
    approval = view["approval"]
    assert approval["matched_breakpoint"]["label"] == "Pause before DELETE or DROP operations."
    assert approval["matched_breakpoint"]["operation_types"] == ["delete", "drop"]
    assert approval["mandatory"] is True
    assert "Breakpoint" in view["status_label"]
    assert _sandbox_counts(service, run_id) == (99441, 625)

    facts = {fact["label"]: fact["value"] for fact in approval["facts"]}
    assert facts["Affected rows"] == "625"
    assert "No rows have been changed" in approval["label"]
    assert view["capabilities"]["continue"] is False
    assert view["capabilities"]["approve"] is True
    assert view["capabilities"]["reject"] is True

    with pytest.raises(DebuggerError) as caught:
        service.continue_run(run_id)
    assert caught.value.code == "approval_pending"
    with pytest.raises(DebuggerError) as caught:
        service.step_over(run_id)
    assert caught.value.code == "approval_pending"
    assert _sandbox_counts(service, run_id) == (99441, 625)


def test_breakpoint_gate_allows_feedback_without_executing(service):
    run_id, view = _reach_breakpoint_delete(service)
    approval_id = view["approval"]["id"]

    after_feedback = service.feedback(
        run_id,
        "step_3",
        "Do not delete these rows; propose a safer verification instead.",
    )
    assert after_feedback["approval"] is not None
    assert after_feedback["approval"]["id"] == approval_id
    assert after_feedback["status"] == "awaiting_approval"
    assert after_feedback["revisions"][-1]["status"] == "no_change"
    assert _sandbox_counts(service, run_id) == (99441, 625)
    assert any(
        event["type"] == "breakpoint_feedback"
        for event in service.events.tail(run_id, 30)
    )

    # Feedback does not resolve the gate: only Approve or Reject can do that.
    with pytest.raises(DebuggerError) as caught:
        service.continue_run(run_id)
    assert caught.value.code == "approval_pending"


def test_breakpoint_gate_approve_executes_once_and_records_rule(service):
    run_id, view = _reach_breakpoint_delete(service)
    rule_id = view["approval"]["matched_breakpoint"]["id"]

    approved = service.approve(run_id)
    assert approved["approval"] is None
    assert approved["approval_history"][-1]["status"] == "approved"
    assert approved["approval_history"][-1]["matched_breakpoint"]["id"] == rule_id
    assert _sandbox_counts(service, run_id) == (98816, 0)

    with pytest.raises(DebuggerError):
        service.approve(run_id)
    assert _sandbox_counts(service, run_id) == (98816, 0)


def test_breakpoint_gate_reject_does_not_execute(service):
    run_id, _ = _reach_breakpoint_delete(service)
    rejected = service.reject_approval(run_id, "Not authorized in this demo")
    assert rejected["approval"] is None
    assert rejected["approval_history"][-1]["status"] == "rejected"
    assert rejected["approval_history"][-1]["matched_breakpoint"] is not None
    assert _sandbox_counts(service, run_id) == (99441, 625)


def test_removing_breakpoint_does_not_remove_pending_mandatory_gate(service):
    run_id, view = _reach_breakpoint_delete(service)
    breakpoint_id = view["breakpoints"][0]["id"]
    after_removal = service.remove_breakpoint(run_id, breakpoint_id)
    assert after_removal["breakpoints"] == []
    assert after_removal["status"] == "awaiting_approval"
    assert after_removal["approval"] is not None
    assert after_removal["approval"]["mandatory"] is True
    assert after_removal["approval"]["matched_breakpoint"] is not None
    assert _sandbox_counts(service, run_id) == (99441, 625)

    # The mandatory gate remains enforceable with no user breakpoint active.
    approved = service.approve(run_id)
    assert approved["approval"] is None
    assert _sandbox_counts(service, run_id) == (98816, 0)


def test_breakpoint_can_be_disabled_and_reenabled_via_api(api):
    view = api.post(
        "/api/runs",
        json={
            "task": "Delete canceled test orders from the disposable sandbox.",
            "mode": "scripted",
            "dataset": "sandbox",
        },
    ).json()
    run_id = view["id"]
    view = api.post(
        f"/api/runs/{run_id}/breakpoints",
        json={"text": "Pause before DELETE or DROP operations."},
    ).json()
    breakpoint_id = view["breakpoints"][0]["id"]

    disabled = api.request(
        "PATCH",
        f"/api/runs/{run_id}/breakpoints/{breakpoint_id}",
        json={"active": False, "command_id": "api-breakpoint-disable-1"},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["breakpoints"][0]["active"] is False

    # A disabled rule is ignored by the controller, but the mandatory gate
    # still fires for the destructive write.
    api.post(f"/api/runs/{run_id}/step-over", json={"command_id": "step-1"})
    api.post(f"/api/runs/{run_id}/step-over", json={"command_id": "step-2"})
    api.post(f"/api/runs/{run_id}/step-over", json={"command_id": "step-3"})
    gate = api.get(f"/api/runs/{run_id}").json()
    assert gate["status"] == "awaiting_approval"
    assert gate["approval"]["matched_breakpoint"] is None
    assert gate["approval"]["mandatory"] is True

    enabled = api.request(
        "PATCH",
        f"/api/runs/{run_id}/breakpoints/{breakpoint_id}",
        json={"active": True, "command_id": "api-breakpoint-enable-1"},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["breakpoints"][0]["active"] is True


def test_disabling_breakpoint_keeps_pending_gate(service):
    run_id, view = _reach_breakpoint_delete(service)
    breakpoint_id = view["breakpoints"][0]["id"]
    after = service.set_breakpoint_active(run_id, breakpoint_id, False)
    assert after["breakpoints"][0]["active"] is False
    assert after["status"] == "awaiting_approval"
    assert after["approval"]["matched_breakpoint"]["id"] == breakpoint_id
    assert after["approval"]["mandatory"] is True
    assert _sandbox_counts(service, run_id) == (99441, 625)

    approved = service.approve(run_id)
    assert approved["approval"] is None
    assert _sandbox_counts(service, run_id) == (98816, 0)


def test_mandatory_gate_still_applies_without_breakpoints(service):
    view = service.create_run(
        "Delete canceled test orders from the disposable sandbox and verify the remaining rows.",
        "scripted",
        "sandbox",
    )
    run_id = view["id"]
    service.step_over(run_id)
    service.step_over(run_id)
    service.step_over(run_id)
    view = service.get_view(run_id)
    assert view["status"] == "awaiting_approval"
    assert view["approval"]["matched_breakpoint"] is None
    assert view["approval"]["mandatory"] is True
    assert view["breakpoints"] == []
    with pytest.raises(DebuggerError) as caught:
        service.continue_run(run_id)
    assert caught.value.code == "approval_pending"
    assert _sandbox_counts(service, run_id) == (99441, 625)


def test_data_effect_watchpoint_pauses_before_payment_join(service):
    view = service.create_run(
        "Show monthly product category sales from delivered orders.",
        "scripted",
    )
    run_id = view["id"]
    assert view["scenario_id"] == "category_sales"
    view = service.add_breakpoint(run_id, "Pause if a payment record is repeated.")

    for _ in range(12):
        if view["status"] in {"awaiting_approval", "completed", "failed"}:
            break
        view = service.step_over(run_id)

    assert view["status"] == "awaiting_approval"
    approval = view["approval"]
    assert approval is not None
    assert approval["tool"] == "join_tables"
    assert approval["mandatory"] is False
    assert approval["matched_breakpoint"]["kind"] == "data_effect"
    assert "order_payments" in str(approval["args"])
    facts = {fact["label"]: fact["value"] for fact in approval["facts"]}
    assert facts["Executed"] == "No"
    assert int(facts["Preview max copies / payment"].replace(",", "")) > 1
    assert facts["Watchpoint threshold"] == "> 1"

    approved = service.approve(run_id)
    assert approved["approval"] is None
    payment_joins = [
        operation
        for step in approved["steps"]
        for operation in step["operations"]
        if operation["tool"] == "join_tables"
        and "order_payments" in str(operation["args"])
    ]
    assert payment_joins
    assert payment_joins[0]["status"] == "completed"
