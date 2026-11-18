from __future__ import annotations

import pytest

import backend.app.runtime as runtime_module
from backend.app.llm import LLMFormatError, _normalise_steps
from backend.app.runtime import DebuggerError
from backend.tests.conftest import complete_run


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.available = True
        self.provider = "fake"
        self.model = "fake-planner-1"

    def complete_json(self, system, user, max_tokens=5000):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return self.responses.pop(0)


def live_wrong_plan_payload():
    return {
        "supported": True,
        "task_spec": {
            "objective": "Compute the share of customers who ordered more than once.",
            "population": "Delivered orders.",
            "time_scope": "All purchase timestamps in the Olist dataset.",
            "metric_definitions": [
                {
                    "name": "repeat_purchase_rate",
                    "definition": "Share of customer groups with at least two delivered orders.",
                    "unit": "percent",
                }
            ],
            "semantic_bindings": [
                {
                    "concept": "Customer Identity",
                    "source_table": "orders",
                    "column": "customer_id",
                    "note": "Order-associated customer record key.",
                }
            ],
            "assumptions": ["Status = delivered."],
        },
        "workflow": {
            "planner_note": "Model-generated plan for controller validation.",
            "steps": [
                {
                    "title": "Understand Request",
                    "goal": "State the metric and customer identity.",
                    "understanding": "I will count delivered orders per customer.",
                    "target_data": ["Metric: repeat purchase rate"],
                    "rationale": "Make the interpretation explicit.",
                    "operations": [],
                },
                {
                    "title": "Explore Data",
                    "goal": "Inspect identifiers.",
                    "understanding": "I am checking the identifier columns.",
                    "target_data": ["orders.customer_id", "customers.customer_unique_id"],
                    "rationale": "Verify fields.",
                    "operations": [
                        {
                            "tool": "inspect_schema",
                            "args": {
                                "table": "customers",
                                "columns": ["customer_id", "customer_unique_id"],
                            },
                        },
                        {
                            "tool": "profile_column",
                            "args": {"table": "orders", "column": "customer_id", "top_n": 3},
                        },
                    ],
                },
                {
                    "title": "Prepare Customer Counts",
                    "goal": "Count delivered orders per customer.",
                    "understanding": "I will filter delivered orders and count by customer_id.",
                    "target_data": ["orders", "group by customer_id"],
                    "rationale": "Build the metric input.",
                    "operations": [
                        {
                            "tool": "filter_rows",
                            "args": {
                                "table": "orders",
                                "where": [{"column": "order_status", "op": "eq", "value": "delivered"}],
                                "output": "live_included_orders",
                            },
                        },
                        {
                            "tool": "group_and_aggregate",
                            "args": {
                                "table": "live_included_orders",
                                "by": ["customer_id"],
                                "aggregations": [
                                    {"column": "order_id", "func": "count", "alias": "order_count"}
                                ],
                                "output": "live_customer_counts",
                            },
                        },
                    ],
                },
                {
                    "title": "Compute Repeat Rate",
                    "goal": "Apply the metric definition.",
                    "understanding": "The denominator is the number of customer groups.",
                    "target_data": ["live_customer_counts"],
                    "rationale": "Compute the metric.",
                    "operations": [
                        {
                            "tool": "compute_metric",
                            "args": {
                                "table": "live_customer_counts",
                                "name": "repeat_purchase_rate",
                                "kind": "share_of_rows",
                                "where": [{"column": "order_count", "op": "gte", "value": 2}],
                                "unit": "percent",
                                "definition": "Share of customer groups with at least two delivered orders.",
                                "output": "live_metric",
                            },
                        }
                    ],
                },
                {
                    "title": "Generate Report",
                    "goal": "Summarize the metric.",
                    "understanding": "I will report the computed metric.",
                    "target_data": ["repeat_purchase_rate"],
                    "rationale": "Present the result.",
                    "operations": [
                        {
                            "tool": "compose_report",
                            "args": {
                                "title": "Repeat purchase rate",
                                "metric_names": ["repeat_purchase_rate"],
                                "summary": "Repeat purchase rate from the live plan.",
                            },
                        }
                    ],
                },
            ],
        },
    }


def live_correcting_revision_payload():
    return {
        "explanation": (
            "The identifier evidence shows that orders.customer_id is one customer record per order, "
            "so repeat purchase cannot be observed. I will join customers and group by customer_unique_id."
        ),
        "investigations": [],
        "semantic_bindings": [
            {
                "concept": "Customer Identity",
                "source_table": "customers",
                "column": "customer_unique_id",
                "note": "Persistent identity reached through orders.customer_id.",
            }
        ],
        "metric_definitions": [
            {
                "name": "repeat_purchase_rate",
                "definition": (
                    "Persistent customers with at least two delivered orders divided by persistent "
                    "customers with at least one delivered order."
                ),
                "unit": "percent",
            }
        ],
        "assumptions": ["Status = delivered.", "Persistent identity = customers.customer_unique_id."],
        "step_updates": [
            {
                "step_id": "step_3",
                "understanding": "Join delivered orders to customers and count orders per persistent customer.",
                "operations": [
                    {
                        "tool": "filter_rows",
                        "args": {
                            "table": "orders",
                            "where": [{"column": "order_status", "op": "eq", "value": "delivered"}],
                            "output": "live_included_orders",
                        },
                    },
                    {
                        "tool": "join_tables",
                        "args": {
                            "left": "live_included_orders",
                            "right": "customers",
                            "left_on": "customer_id",
                            "right_on": "customer_id",
                            "how": "inner",
                            "output": "live_joined_customers",
                        },
                    },
                    {
                        "tool": "group_and_aggregate",
                        "args": {
                            "table": "live_joined_customers",
                            "by": ["customer_unique_id"],
                            "aggregations": [
                                {"column": "order_id", "func": "count", "alias": "order_count"}
                            ],
                            "output": "live_customer_counts",
                        },
                    },
                ],
            },
            {
                "step_id": "step_4",
                "understanding": "Repeat rate is the share of persistent customer groups with at least two orders.",
                "operations": [
                    {
                        "tool": "compute_metric",
                        "args": {
                            "table": "live_customer_counts",
                            "name": "repeat_purchase_rate",
                            "kind": "share_of_rows",
                            "where": [{"column": "order_count", "op": "gte", "value": 2}],
                            "unit": "percent",
                            "definition": (
                                "Persistent customers with at least two delivered orders divided by persistent "
                                "customers with at least one delivered order."
                            ),
                            "output": "live_metric",
                        },
                    }
                ],
            },
        ],
    }


def test_live_mode_unavailable_without_key(service):
    with pytest.raises(DebuggerError) as caught:
        service.create_run("What is our customer repeat purchase rate?", "live")
    assert caught.value.status_code == 503
    assert caught.value.code == "live_unavailable"
    assert "API key" in caught.value.message


def test_live_workflow_is_limited_to_high_level_stages():
    steps = [
        {
            "title": f"Stage {index}",
            "goal": "",
            "understanding": "",
            "target_data": [],
            "rationale": "",
            "operations": [],
        }
        for index in range(6)
    ]
    with pytest.raises(LLMFormatError, match="3 to 5 high-level stages"):
        _normalise_steps(steps)


def test_live_mode_uses_llm_plan_validation_and_revision(service, monkeypatch):
    monkeypatch.setattr(runtime_module, "LIVE_AVAILABLE", True)
    fake = FakeLLM([live_wrong_plan_payload(), live_correcting_revision_payload()])
    service.llm = fake

    view = service.create_run("What is our customer repeat purchase rate?", "live")
    assert view["mode"] == "live"
    assert "Live agent" in view["planner_label"]
    assert view["scenario_id"] == "live_repeat_purchase"
    assert view["reference_kind"] == "repeat_purchase"
    assert len(view["steps"]) == 5
    assert len(fake.calls) == 1

    view = complete_run(service, view["id"])
    assert view["metrics"]["repeat_purchase_rate"]["value"] == 0.0
    assert view["reference_check"]["agreement"] is False

    proposal_view = service.feedback(
        view["id"],
        view["steps"][2]["id"],
        "customer_id is one record per order. Use customers.customer_unique_id for the persistent customer.",
    )
    assert len(fake.calls) == 2
    proposal = proposal_view["revisions"][-1]
    assert proposal["status"] == "pending"
    assert proposal["execution_changed"] is True
    assert any(change["kind"] == "binding" for change in proposal["changes"])
    assert proposal["proposed_bindings"][0]["column"] == "customer_unique_id"

    accepted = service.accept_revision(view["id"], proposal["id"])
    assert accepted["capabilities"]["replay"] is True
    result = service.replay(view["id"])
    child = result["run"]
    assert child["status"] == "completed"
    assert child["metrics"]["repeat_purchase_rate"]["formatted"] == "3.0003%"
    assert child["reference_check"]["agreement"] is True
    assert result["comparison"]["child_reference_check"]["agreement"] is True


def live_table_report_plan_payload():
    return {
        "supported": True,
        "task_spec": {
            "objective": "Show monthly product category sales from delivered orders.",
            "population": "Delivered orders and their item rows.",
            "time_scope": "All available purchase dates, grouped by calendar month.",
            "metric_definitions": [
                {
                    "name": "monthly_category_sales",
                    "definition": (
                        "SUM(order_items.price) for delivered orders, grouped by month and "
                        "product category."
                    ),
                    "unit": "currency",
                }
            ],
            "semantic_bindings": [
                {
                    "concept": "delivered order population",
                    "source_table": "orders",
                    "column": "order_status",
                    "note": "Only delivered orders are in scope.",
                },
                {
                    "concept": "sales amount",
                    "source_table": "order_items",
                    "column": "price",
                    "note": "Item price is the sales measure.",
                },
            ],
            "assumptions": ["Sales exclude freight.", "Missing categories remain visible."],
        },
        "workflow": {
            "planner_note": "A table-shaped result is reported from the grouped output table.",
            "steps": [
                {
                    "title": "Understand Request",
                    "goal": "Interpret the monthly category sales request.",
                    "understanding": "The request asks for monthly sales by product category.",
                    "target_data": ["Metric: monthly category sales"],
                    "rationale": "Make the interpretation explicit.",
                    "operations": [],
                },
                {
                    "title": "Prepare Category Sales",
                    "goal": "Build delivered-order item rows and aggregate them.",
                    "understanding": "This stage filters delivered orders, joins item and category data, and groups the result.",
                    "target_data": ["orders", "order_items", "products", "monthly_category_sales"],
                    "rationale": "Build the table the report will describe.",
                    "operations": [
                        {
                            "tool": "filter_rows",
                            "args": {
                                "table": "orders",
                                "where": [{"column": "order_status", "op": "eq", "value": "delivered"}],
                                "output": "live_delivered_orders",
                            },
                        },
                        {
                            "tool": "join_tables",
                            "args": {
                                "left": "live_delivered_orders",
                                "right": "order_items",
                                "left_on": "order_id",
                                "right_on": "order_id",
                                "how": "inner",
                                "output": "live_delivered_items",
                            },
                        },
                        {
                            "tool": "join_tables",
                            "args": {
                                "left": "live_delivered_items",
                                "right": "products",
                                "left_on": "product_id",
                                "right_on": "product_id",
                                "how": "left",
                                "output": "live_item_categories",
                            },
                        },
                        {
                            "tool": "group_and_aggregate",
                            "args": {
                                "table": "live_item_categories",
                                "by": ["product_category_name"],
                                "date_part": {
                                    "column": "order_purchase_timestamp",
                                    "alias": "order_month",
                                    "unit": "month",
                                },
                                "aggregations": [
                                    {"column": "price", "func": "sum", "alias": "category_sales"}
                                ],
                                "output": "monthly_category_sales",
                            },
                        },
                    ],
                },
                {
                    "title": "Report Category Sales",
                    "goal": "Report the table-shaped sales result.",
                    "understanding": "This stage reports the monthly sales table.",
                    "target_data": ["monthly_category_sales"],
                    "rationale": "The table is the requested result.",
                    "operations": [
                        {
                            "tool": "compose_report",
                            "args": {
                                "title": "Monthly product category sales",
                                "metric_names": ["monthly_category_sales"],
                                "summary": "Monthly sales by category for delivered orders.",
                            },
                        }
                    ],
                },
            ],
        },
    }


def test_live_mode_accepts_table_shaped_reports(service, monkeypatch):
    monkeypatch.setattr(runtime_module, "LIVE_AVAILABLE", True)
    service.llm = FakeLLM([live_table_report_plan_payload()])

    view = service.create_run(
        "Show monthly product category sales from delivered orders.", "live"
    )
    view = complete_run(service, view["id"])
    assert view["report"] is not None
    assert view["report"]["metrics"] == []
    assert view["report"]["table_name"] == "monthly_category_sales"
    assert view["report"]["table_rows"] > 0
    assert view["reference_check"]["comparison_error"] is None
    assert view["reference_check"]["grouped_agreement"] is True
    assert view["reference_check"]["mismatched_groups"] == 0


def test_live_mode_rejects_malformed_plans(service, monkeypatch):
    monkeypatch.setattr(runtime_module, "LIVE_AVAILABLE", True)
    service.llm = FakeLLM(
        [
            {
                "supported": True,
                "task_spec": {"objective": "", "population": "", "time_scope": ""},
                "workflow": {"steps": []},
            }
        ]
    )
    with pytest.raises(DebuggerError) as caught:
        service.create_run("A task", "live")
    assert caught.value.code == "invalid_live_plan"


def test_live_mode_rejects_unsupported_tool_plans(service, monkeypatch):
    monkeypatch.setattr(runtime_module, "LIVE_AVAILABLE", True)
    payload = live_wrong_plan_payload()
    payload["workflow"]["steps"][2]["operations"] = [
        {"tool": "run_python", "args": {"code": "import os"}}
    ]
    service.llm = FakeLLM([payload])
    with pytest.raises(DebuggerError) as caught:
        service.create_run("What is our customer repeat purchase rate?", "live")
    assert caught.value.code == "invalid_live_plan"
    assert "unsupported tool" in caught.value.message


def test_live_destructive_decline_points_to_the_prepared_case_c(service, monkeypatch):
    monkeypatch.setattr(runtime_module, "LIVE_AVAILABLE", True)
    service.llm = FakeLLM(
        [
            {
                "supported": False,
                "unsupported_reason": "Deleting rows is destructive and no delete tool is available.",
            }
        ]
    )
    with pytest.raises(DebuggerError) as caught:
        service.create_run(
            "Clean up our customer table: delete customers who have not placed an order "
            "in the last 12 months.",
            "live",
        )
    assert caught.value.code == "invalid_live_plan"
    assert "prepared Case C scenario" in caught.value.message
    assert "Customer cleanup" in caught.value.message
