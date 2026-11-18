from __future__ import annotations

import pandas as pd

from backend.app.tools import execute_tool
from backend.tests.conftest import complete_run


def test_compose_report_can_report_a_table_without_a_scalar_metric():
    tables = {
        "monthly_category_sales": pd.DataFrame(
            {
                "month": ["2024-01", "2024-02"],
                "product_category_name": ["toys", "books"],
                "sales": [10.0, 20.0],
            }
        )
    }
    result = execute_tool(
        "compose_report",
        {
            "title": "Monthly category sales",
            "metric_names": ["monthly_category_sales"],
            "summary": "Monthly sales by category.",
        },
        tables,
        dataset=None,
    )
    assert result.report is not None
    assert result.report.metrics == []
    assert result.report.table_name == "monthly_category_sales"
    assert result.report.table_rows == 2
    assert result.evidence[0].details["table_name"] == "monthly_category_sales"

    explicit = execute_tool(
        "compose_report",
        {
            "title": "Monthly category sales",
            "metric_names": [],
            "table": "monthly_category_sales",
        },
        tables,
        dataset=None,
    )
    assert explicit.report is not None
    assert explicit.report.table_name == "monthly_category_sales"


def test_case_b_payment_grain_inflation_then_price_revision(service):
    view = service.create_run("Show monthly product category sales from delivered orders.", "scripted")
    run_id = view["id"]
    assert view["scenario_id"] == "category_sales"

    view = complete_run(service, run_id)
    metric = view["metrics"]["total_sales"]
    assert metric["formatted"] == "R$ 19,776,160.44"
    assert metric["value"] == 19776160.44

    # The payments join records real multiplicity in the evidence rather than hiding it.
    join_evidence = [
        item
        for item in view["steps"][2]["evidence"]
        if item["status"] == "OBSERVED" and item["kind"] == "join"
    ]
    assert len(join_evidence) >= 2
    payment_facts = {fact["label"]: fact["value"] for item in join_evidence for fact in item["facts"]}
    assert any("repeated" in label.lower() for label in payment_facts)

    check = view["reference_check"]
    assert check["agreement"] is False
    assert check["reference_formatted"] == "R$ 13,221,498.11"
    assert check["workflow_formatted"] == "R$ 19,776,160.44"
    assert check["diagnostics"]["delivered_payment_records"] == 100756
    assert check["diagnostics"]["item_grain_payment_rows"] == 115035
    assert check["diagnostics"]["distinct_delivered_payment_total"] == 15422461.77
    assert check["diagnostics"]["item_grain_payment_total"] == 19776160.44
    assert check["mismatched_groups"] > 0
    assert check["grouped_agreement"] is False

    proposal_view = service.feedback(
        run_id,
        view["steps"][3]["id"],
        "The payments join duplicates payment_value across item rows. Sales should be SUM(order_items.price), not payment_value.",
    )
    proposal = proposal_view["revisions"][-1]
    assert proposal["execution_changed"] is True
    assert proposal["earliest_affected_step"] == 2
    assert any(change["kind"] == "binding" for change in proposal["changes"])
    assert service.get_view(run_id)["metrics"]["total_sales"]["value"] == 19776160.44

    accepted = service.accept_revision(run_id, proposal["id"])
    assert accepted["capabilities"]["replay"] is True
    assert accepted["metrics"]["total_sales"]["value"] == 19776160.44

    result = service.replay(run_id)
    child = result["run"]
    comparison = result["comparison"]
    child_metric = child["metrics"]["total_sales"]
    assert child_metric["formatted"] == "R$ 13,221,498.11"
    assert child["reference_check"]["agreement"] is True
    assert child["reference_check"]["grouped_agreement"] is True
    assert child["reference_check"]["mismatched_groups"] == 0
    assert child["report"] is not None

    steps = {item["step_number"]: item for item in comparison["plan_diff"]}
    assert steps[3]["changed_operations"] is True
    assert steps[3]["old_operations"] != steps[3]["new_operations"]
    metric_diff = comparison["metric_definitions_diff"][0]
    assert metric_diff["name"] == "total_sales"
    assert metric_diff["old_formatted"] == "R$ 19,776,160.44"
    assert metric_diff["new_formatted"] == "R$ 13,221,498.11"
    assert comparison["parent_reference_check"]["agreement"] is False
    assert comparison["child_reference_check"]["agreement"] is True
    # No payment join remains in the replayed build step.
    replayed_build = child["steps"][2]
    assert all(op["tool"] != "join_tables" or "order_payments" not in str(op["args"]) for op in replayed_build["operations"])
