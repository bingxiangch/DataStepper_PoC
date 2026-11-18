"""Controlled scripted planners and prepared revision proposals.

Scripted mode fixes the *plan choices* so demos are reproducible.  Every data
operation still runs through the real executor and every number is measured
from the Olist CSVs.  A scripted interpretation is never presented as an
autonomous model choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from .dataset import OlistDataset
from .models import (
    Fact,
    Investigation,
    MetricDefinition,
    RevisionChange,
    SemanticBinding,
    StepUpdate,
)
from .tools import fmt_currency, fmt_int


class ScenarioError(RuntimeError):
    pass


@dataclass
class PlanDraft:
    scenario_id: str
    reference_kind: str | None
    task_spec: dict[str, Any]
    steps: list[dict[str, Any]]
    planner_label: str
    notes: list[str] = field(default_factory=list)
    # Guards declared by a prepared scenario. They are real, user-visible
    # BreakpointRules created with the run, not hidden execution conditions.
    initial_breakpoints: list[str] = field(default_factory=list)


A_BINDING = SemanticBinding(
    concept="Customer Identity",
    source_table="orders",
    column="customer_id",
    status="proposed",
    note="Initial plan groups delivered orders directly on the order-associated customer record key.",
)
A_METRIC = MetricDefinition(
    name="repeat_purchase_rate",
    definition=(
        "Number of customers with at least 2 delivered orders divided by the number of customers "
        "with at least 1 delivered order, using the current Customer Identity binding."
    ),
    unit="percent",
    status="proposed",
)


def _step(
    title: str,
    goal: str,
    understanding: str,
    target_data: list[str],
    rationale: str,
    operations: list[dict[str, Any]],
    dependency_chain: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "title": title,
        "goal": goal,
        "understanding": understanding,
        "target_data": target_data,
        "rationale": rationale,
        "operations": operations,
        "dependency_chain": dependency_chain,
    }


def _repeat_purchase_plan(dataset: OlistDataset) -> PlanDraft:
    steps = [
        _step(
            title="Understand Task",
            goal="Make the task interpretation and metric definition explicit before touching data.",
            understanding=(
                "The request asks for the customer repeat purchase rate: the share of purchasing customers "
                "with at least two orders, across all purchase dates. I am assuming delivered orders are the "
                "relevant population and that a customer can be recognized across orders; neither assumption is confirmed yet."
            ),
            target_data=[
                "Task: customer repeat purchase rate",
                "Binding: Customer Identity → orders.customer_id",
                "Metric: repeat customers / customers with an order",
                "Population: delivered orders, all purchase dates",
            ],
            rationale="The order table already contains customer_id, so the initial plan can group orders directly without a join.",
            operations=[],
            dependency_chain=[
                {"label": "Customer identity binding", "step_id": "step_1"},
                {"label": "Group orders", "step_id": "step_3"},
                {"label": "Repeat rate", "step_id": "step_4"},
            ],
        ),
        _step(
            title="Explore Data",
            goal="Verify the source columns and inspect the scale of the order and customer tables.",
            understanding=(
                "To test the customer-identity assumption, this stage inspects the order and customer tables. "
                "I will check order status, purchase time, and the documented customer identifiers before choosing "
                "the key the preparation stage will use."
            ),
            target_data=[
                "Tables: orders, customers",
                "Fields: orders.customer_id, customers.customer_id, customers.customer_unique_id",
                "Operation: inspect schema (read-only)",
            ],
            rationale="Schema inspection establishes which columns are available and how identifiers are documented.",
            operations=[
                {"tool": "inspect_schema", "args": {"table": "orders", "columns": ["order_id", "customer_id", "order_status", "order_purchase_timestamp"]}},
                {"tool": "inspect_schema", "args": {"table": "customers", "columns": ["customer_id", "customer_unique_id"]}},
                {"tool": "profile_column", "args": {"table": "orders", "column": "customer_id", "top_n": 5}},
            ],
            dependency_chain=[
                {"label": "Source schema", "step_id": "step_2"},
                {"label": "Prepare customer data", "step_id": "step_3"},
            ],
        ),
        _step(
            title="Prepare Customer Data",
            goal="Count delivered orders per customer using the current identity binding.",
            understanding=(
                "The request fixes only the metric; the delivered-order scope and the customer identity are "
                "still agent assumptions. I will filter delivered orders, group them by orders.customer_id, and "
                "count orders per group to prepare the metric input."
            ),
            target_data=[
                "Tables: orders",
                "Key field: customer_id (one customer record per order)",
                "Operation: filter delivered orders; group by customer_id; count order_id",
                "Output: one row per customer identity with order_count",
            ],
            rationale="Grouping by the bound identity turns order records into the per-customer order counts the metric needs.",
            operations=[
                {
                    "tool": "filter_rows",
                    "args": {
                        "table": "orders",
                        "where": [{"column": "order_status", "op": "eq", "value": "delivered"}],
                        "output": "included_orders",
                    },
                },
                {
                    "tool": "group_and_aggregate",
                    "args": {
                        "table": "included_orders",
                        "by": ["customer_id"],
                        "aggregations": [{"column": "order_id", "func": "count", "alias": "order_count"}],
                        "output": "customer_order_counts",
                    },
                },
            ],
            dependency_chain=[
                {"label": "Customer identity (customer_id)", "step_id": "step_1"},
                {"label": "Group orders", "step_id": "step_3"},
                {"label": "Repeat rate", "step_id": "step_4"},
            ],
        ),
        _step(
            title="Compute Repeat Purchase Rate",
            goal="Turn per-customer order counts into the declared repeat-purchase metric.",
            understanding=(
                "This stage applies the requested repeat-purchase definition to the prepared counts. "
                "The rate is the share of customer groups with at least two delivered orders; the denominator "
                "follows the same customer-identity assumption."
            ),
            target_data=[
                "Input: customer_order_counts.order_count",
                "Metric: share of groups with order_count ≥ 2",
                "Output: repeat_purchase_rate",
            ],
            rationale="This is the declared metric definition applied to the prepared customer table.",
            operations=[
                {
                    "tool": "compute_metric",
                    "args": {
                        "table": "customer_order_counts",
                        "name": "repeat_purchase_rate",
                        "kind": "share_of_rows",
                        "where": [{"column": "order_count", "op": "gte", "value": 2}],
                        "unit": "percent",
                        "definition": A_METRIC.definition,
                        "numerator_label": "Repeat customers",
                        "denominator_label": "Customers with an order",
                        "output": "metric_repeat_rate",
                    },
                }
            ],
            dependency_chain=[
                {"label": "Order counts", "step_id": "step_3"},
                {"label": "Repeat rate", "step_id": "step_4"},
                {"label": "Report", "step_id": "step_5"},
            ],
        ),
        _step(
            title="Generate Report",
            goal="Report the measured repeat-purchase rate with its definition.",
            understanding=(
                "This final stage reports the measured rate together with the delivered-order population and "
                "customer-identity assumption behind it, so the result can be checked against the request."
            ),
            target_data=[
                "Metric: repeat_purchase_rate (observed)",
                "Report: value, denominator, definition",
            ],
            rationale="The report is composed only from metrics already computed by the workflow.",
            operations=[
                {
                    "tool": "compose_report",
                    "args": {
                        "title": "Customer repeat purchase rate",
                        "metric_names": ["repeat_purchase_rate"],
                        "summary": "Repeat purchase rate computed from delivered orders and the current customer-identity binding.",
                        "notes": ["Population: delivered orders with purchase timestamps in the Olist dataset."],
                    },
                }
            ],
            dependency_chain=[
                {"label": "Repeat rate", "step_id": "step_4"},
                {"label": "Report", "step_id": "step_5"},
            ],
        ),
    ]
    return PlanDraft(
        scenario_id="repeat_purchase",
        reference_kind="repeat_purchase",
        task_spec={
            "objective": "Compute the customer repeat purchase rate.",
            "population": "Delivered orders; customers with at least one delivered order.",
            "time_scope": "All order purchase timestamps in the Olist dataset.",
            "metric_definitions": [A_METRIC],
            "semantic_bindings": [A_BINDING],
            "assumptions": [
                "Included order statuses = delivered.",
                "A customer with at least two included orders is a repeat customer.",
                "Customer Identity is stable across multiple orders (to be validated).",
            ],
        },
        steps=steps,
        planner_label="Scripted scenario planner · controlled customer_id interpretation · real data execution",
    )


def _category_sales_plan(dataset: OlistDataset) -> PlanDraft:
    measure_binding = SemanticBinding(
        concept="Sales Measure",
        source_table="order_payments",
        column="payment_value",
        status="proposed",
        note="Initial plan sums order-level payment amounts after attaching item rows.",
    )
    grain_binding = SemanticBinding(
        concept="Sales Grain",
        source_table="order_items",
        column="order_id",
        status="proposed",
        note="Rows are items joined to order-level payment records; payment values can repeat across item rows.",
    )
    metric = MetricDefinition(
        name="total_sales",
        definition=(
            "Monthly product sales by category = SUM(order_payments.payment_value) grouped by purchase "
            "month and product category after joining orders, items, payments, and products."
        ),
        unit="currency",
        status="proposed",
    )
    steps = [
        _step(
            title="Understand Task",
            goal="Make the sales measure, grain, and monthly category grouping explicit.",
            understanding=(
                "The request asks for monthly product sales by product category. I am assuming delivered orders "
                "are the relevant population, that sales means the amount paid by customers, and that purchase "
                "month is the reporting period; the exact measure and grain still need validation."
            ),
            target_data=[
                "Task: monthly product sales by category",
                "Measure: order_payments.payment_value",
                "Dimensions: purchase month, product category",
                "Population: delivered orders",
            ],
            rationale="Payment values are available at order level and can be joined to item/category rows.",
            operations=[],
            dependency_chain=[
                {"label": "Sales measure binding", "step_id": "step_1"},
                {"label": "Build sales detail", "step_id": "step_3"},
                {"label": "Monthly aggregation", "step_id": "step_4"},
            ],
        ),
        _step(
            title="Explore Data",
            goal="Inspect order, item, payment, product, and category columns before joining.",
            understanding=(
                "To choose a defensible sales measure, this stage inspects the order, item, payment, and product "
                "tables. I will check order status and purchase time, item price fields, payment-record fields, "
                "and category labels before any join."
            ),
            target_data=[
                "Tables: orders, order_items, order_payments, products",
                "Fields: purchase timestamp, price/freight, payment_value, product_category_name",
                "Operation: inspect schema (read-only)",
            ],
            rationale="The joins must use documented keys and retain the item and payment record keys needed to inspect grain.",
            operations=[
                {"tool": "inspect_schema", "args": {"table": "orders", "columns": ["order_id", "order_status", "order_purchase_timestamp"]}},
                {"tool": "inspect_schema", "args": {"table": "order_items", "columns": ["order_id", "order_item_id", "product_id", "price", "freight_value"]}},
                {"tool": "inspect_schema", "args": {"table": "order_payments", "columns": ["order_id", "payment_sequential", "payment_type", "payment_value"]}},
                {"tool": "inspect_schema", "args": {"table": "products", "columns": ["product_id", "product_category_name"]}},
            ],
            dependency_chain=[
                {"label": "Source schema", "step_id": "step_2"},
                {"label": "Build sales detail", "step_id": "step_3"},
            ],
        ),
        _step(
            title="Build Sales Dataset",
            goal="Attach items, payments, and categories to delivered orders.",
            understanding=(
                "This stage builds the sales detail needed for the requested month and category view. The "
                "request does not specify price versus payment, so the payment-based measure remains my "
                "assumption. I will keep delivered orders and attach item, payment, and category rows; the "
                "item × payment-record grain makes any duplication visible."
            ),
            target_data=[
                "Tables: orders, order_items, order_payments, products",
                "Measure field: order_payments.payment_value (order-level)",
                "Grain: one row per item × payment record after joining",
                "Operations: filter delivered → join items → join payments → join products",
            ],
            rationale="All four tables are joined before grouping so the category and month attributes are available.",
            operations=[
                {
                    "tool": "filter_rows",
                    "args": {
                        "table": "orders",
                        "where": [{"column": "order_status", "op": "eq", "value": "delivered"}],
                        "output": "included_orders",
                    },
                },
                {
                    "tool": "join_tables",
                    "args": {
                        "left": "included_orders",
                        "right": "order_items",
                        "left_on": "order_id",
                        "right_on": "order_id",
                        "how": "inner",
                        "output": "order_lines",
                    },
                },
                {
                    "tool": "join_tables",
                    "args": {
                        "left": "order_lines",
                        "right": "order_payments",
                        "left_on": "order_id",
                        "right_on": "order_id",
                        "how": "inner",
                        "output": "sales_with_payments",
                    },
                },
                {
                    "tool": "join_tables",
                    "args": {
                        "left": "sales_with_payments",
                        "right": "products",
                        "left_on": "product_id",
                        "right_on": "product_id",
                        "how": "left",
                        "output": "sales_detail",
                    },
                },
            ],
            dependency_chain=[
                {"label": "Sales measure (payment_value)", "step_id": "step_1"},
                {"label": "Item × payment grain", "step_id": "step_3"},
                {"label": "Monthly aggregation", "step_id": "step_4"},
            ],
        ),
        _step(
            title="Aggregate Monthly Category Sales",
            goal="Group sales_detail by purchase month and product category, then compute total sales.",
            understanding=(
                "This stage applies the requested month and category grouping to the assembled rows. "
                "Sales are summed from the current payment-based measure, so the total reflects the "
                "agent's measure assumption."
            ),
            target_data=[
                "Input: sales_detail.payment_value",
                "Group keys: purchase month, product_category_name",
                "Aggregation: SUM(payment_value) AS sales",
                "Outputs: monthly_category_sales, total_sales",
            ],
            rationale="These are the dimensions and measure stated in the initial TaskSpec.",
            operations=[
                {
                    "tool": "group_and_aggregate",
                    "args": {
                        "table": "sales_detail",
                        "by": ["product_category_name"],
                        "date_part": {"column": "order_purchase_timestamp", "alias": "month", "unit": "month"},
                        "aggregations": [{"column": "payment_value", "func": "sum", "alias": "sales"}],
                        "output": "monthly_category_sales",
                    },
                },
                {
                    "tool": "compute_metric",
                    "args": {
                        "table": "monthly_category_sales",
                        "name": "total_sales",
                        "kind": "sum",
                        "column": "sales",
                        "unit": "currency",
                        "definition": metric.definition,
                        "output": "metric_total_sales",
                    },
                },
            ],
            dependency_chain=[
                {"label": "Sales detail", "step_id": "step_3"},
                {"label": "Monthly category sales", "step_id": "step_4"},
                {"label": "Report", "step_id": "step_5"},
            ],
        ),
        _step(
            title="Generate Report",
            goal="Report monthly category sales and the total.",
            understanding=(
                "This final stage reports monthly category sales and the total with the measure, grain, and "
                "delivered-order scope used, so the result can be checked against the request."
            ),
            target_data=[
                "Metric: total_sales (observed)",
                "Table: monthly_category_sales",
                "Report: definition and total",
            ],
            rationale="The report is composed only from computed metrics.",
            operations=[
                {
                    "tool": "compose_report",
                    "args": {
                        "title": "Monthly product sales by category",
                        "metric_names": ["total_sales"],
                        "summary": "Monthly product sales by category computed for delivered orders using the current measure binding.",
                        "notes": ["Grouped by purchase month and product category."],
                    },
                }
            ],
            dependency_chain=[
                {"label": "Monthly category sales", "step_id": "step_4"},
                {"label": "Report", "step_id": "step_5"},
            ],
        ),
    ]
    return PlanDraft(
        scenario_id="category_sales",
        reference_kind="category_sales",
        task_spec={
            "objective": "Compute monthly product sales by product category.",
            "population": "Delivered orders and their item rows.",
            "time_scope": "All order purchase timestamps, grouped by purchase month.",
            "metric_definitions": [metric],
            "semantic_bindings": [
                measure_binding,
                grain_binding,
                SemanticBinding(concept="Product Category", source_table="products", column="product_category_name", status="proposed", note="Category label; missing labels are retained as null."),
                SemanticBinding(concept="Purchase Month", source_table="orders", column="order_purchase_timestamp", status="proposed", note="Calendar month of purchase."),
            ],
            "assumptions": [
                "Included order statuses = delivered.",
                "Payment values may be attributed to category rows after joining item rows (to be validated).",
                "Freight is not separately excluded under the payment-based measure.",
            ],
        },
        steps=steps,
        planner_label="Scripted scenario planner · controlled payment_value interpretation · real data execution",
    )


def _sandbox_plan(dataset: OlistDataset) -> PlanDraft:
    steps = [
        _step(
            title="Understand Request",
            goal="Identify the disposable target and the destructive action.",
            understanding=(
                "The request asks to delete canceled test orders from the disposable sandbox and verify the "
                "remaining rows. I am assuming this is a destructive cleanup that requires explicit developer "
                "approval and must never touch production data."
            ),
            target_data=[
                "Target: sandbox_orders in a per-run SQLite file",
                "Proposed action: DELETE WHERE order_status = 'canceled'",
                "Safety: explicit approval gate; no production database access",
            ],
            rationale="Destructive writes require a pause before execution and a measured affected-row preview.",
            operations=[],
            dependency_chain=[
                {"label": "Sandbox target", "step_id": "step_1"},
                {"label": "Risky DELETE", "step_id": "step_3"},
                {"label": "Verify", "step_id": "step_4"},
            ],
        ),
        _step(
            title="Inspect Sandbox",
            goal="Measure the sandbox population before proposing a write.",
            understanding=(
                "This stage measures the current sandbox so the proposed cleanup can be evaluated before any "
                "write. I will count all rows and the canceled orders that match the deletion condition."
            ),
            target_data=[
                "Table: sandbox_orders",
                "Fields: order_status",
                "Operation: read-only sandbox inspection",
            ],
            rationale="The affected-row preview must be based on the actual sandbox state.",
            operations=[{"tool": "sandbox_inspect", "args": {}}],
            dependency_chain=[
                {"label": "Sandbox state", "step_id": "step_2"},
                {"label": "Risky DELETE", "step_id": "step_3"},
            ],
        ),
        _step(
            title="Delete Canceled Test Orders",
            goal="Execute the proposed destructive cleanup only after explicit approval.",
            understanding=(
                "This stage performs the requested cleanup only after the approval gate. The controller shows "
                "the target rows and affected count and will not execute the DELETE unless the developer approves."
            ),
            target_data=[
                "Table: sandbox_orders (disposable SQLite)",
                "Condition: order_status = 'canceled'",
                "Effect: DELETE matching rows",
                "Gate: explicit Approve / Reject",
            ],
            rationale="This step demonstrates the mandatory approval gate for destructive writes.",
            operations=[
                {
                    "tool": "sandbox_delete_rows",
                    "args": {"where": [{"column": "order_status", "op": "eq", "value": "canceled"}]},
                }
            ],
            dependency_chain=[
                {"label": "Sandbox state", "step_id": "step_2"},
                {"label": "Sandbox DELETE", "step_id": "step_3"},
                {"label": "Verify", "step_id": "step_4"},
            ],
        ),
        _step(
            title="Verify and Report",
            goal="Measure the sandbox after the decision and report the result.",
            understanding=(
                "This final stage re-reads the sandbox after the decision and reports the resulting row count. "
                "If the developer rejects the write, the count should remain unchanged."
            ),
            target_data=[
                "Table: sandbox_orders",
                "Metric: sandbox_rows",
                "Report: final row count and decision context",
            ],
            rationale="Verification is a fresh read of the sandbox, not an assumed effect.",
            operations=[
                {"tool": "sandbox_inspect", "args": {}},
                {
                    "tool": "compose_report",
                    "args": {
                        "title": "Disposable sandbox cleanup",
                        "metric_names": ["sandbox_rows"],
                        "summary": "Final sandbox row count after the approval decision.",
                        "notes": ["This sandbox is disposable and isolated from production data."],
                    },
                },
            ],
            dependency_chain=[
                {"label": "Sandbox DELETE decision", "step_id": "step_3"},
                {"label": "Verified sandbox state", "step_id": "step_4"},
            ],
        ),
    ]
    return PlanDraft(
        scenario_id="sandbox_cleanup",
        reference_kind=None,
        task_spec={
            "objective": "Remove canceled test orders from a disposable SQLite sandbox.",
            "population": "Rows in the per-run sandbox_orders table.",
            "time_scope": "Current sandbox state.",
            "metric_definitions": [],
            "semantic_bindings": [
                SemanticBinding(concept="Target Table", source_table="sandbox_orders", column="order_status", status="proposed", note="Disposable SQLite table in this run directory; never a production database.")
            ],
            "assumptions": [
                "The sandbox is disposable.",
                "A destructive write must be approved explicitly before execution.",
            ],
        },
        steps=steps,
        planner_label="Scripted sandbox planner · destructive-write approval demo",
    )


def _customer_cleanup_plan(dataset: OlistDataset) -> PlanDraft:
    temporal_binding = SemanticBinding(
        concept="Temporal Reference",
        source_table="sandbox_orders",
        column="order_purchase_timestamp",
        status="proposed",
        note="Inactivity is measured 12 months back from the current date at run time.",
    )
    target_binding = SemanticBinding(
        concept="Cleanup Target",
        source_table="sandbox_customers",
        column="customer_id",
        status="proposed",
        note="Rows in the disposable per-run copy of the customer table; never a production database.",
    )
    action_binding = SemanticBinding(
        concept="Cleanup Action",
        source_table="sandbox_customers",
        column="customer_id",
        status="proposed",
        note="DELETE permanently removes each matching customer row from the disposable copy.",
    )
    inactive_metric = MetricDefinition(
        name="inactive_customers",
        definition=(
            "Customer rows whose latest order predates the cutoff 12 months before the declared "
            "reference date; customers with no orders are included."
        ),
        unit="number",
        status="proposed",
    )
    steps = [
        _step(
            title="Understand Request",
            goal="Make the temporal reference and the no-order policy explicit before measuring anything.",
            understanding=(
                "The request asks to delete customers who have not placed an order in the last 12 months. "
                "I am interpreting last 12 months relative to the current date, treating sandbox_customers "
                "as the target, and including customers with no orders at all. The temporal reference is a "
                "plausible reading, but this is a historical dataset, so the resulting affected set must be "
                "measured rather than inferred."
            ),
            target_data=[
                "Task: remove inactive customers",
                "Temporal Reference: current date minus 12 months",
                "Target: sandbox_customers (disposable copy)",
                "Policy: customers with no orders are included",
            ],
            rationale=(
                "A current-date reading can make almost every customer in a historical table inactive; "
                "the next step measures the exact set with a read-only query."
            ),
            operations=[],
            dependency_chain=[
                {"label": "Temporal Reference", "step_id": "step_1"},
                {"label": "Read-only preview", "step_id": "step_2"},
                {"label": "Customer DELETE", "step_id": "step_3"},
                {"label": "Verify", "step_id": "step_4"},
            ],
        ),
        _step(
            title="Preview Affected Customers",
            goal="Resolve the reference date and measure the matching customer rows without writing.",
            understanding=(
                "Before any write, this stage runs an equivalent read-only query against the disposable "
                "customer and order tables. It resolves the declared reference date and cutoff, reports the "
                "dataset's order range, counts customers whose latest order predates the cutoff, and states "
                "how customers with no orders are treated."
            ),
            target_data=[
                "Reference: current date minus 12 months",
                "Predicate: latest order before cutoff; no-order customers included",
                "Output: matching customer count and dataset order range",
                "Effect: none (read-only preview)",
            ],
            rationale="The affected set must come from an actual query over the sandbox, not from the maximum order date alone.",
            operations=[
                {
                    "tool": "sandbox_customer_preview",
                    "args": {
                        "table": "sandbox_customers",
                        "reference": "current_date",
                        "months": 12,
                        "include_never_ordered": True,
                    },
                }
            ],
            dependency_chain=[
                {"label": "Temporal Reference", "step_id": "step_1"},
                {"label": "Measured affected set", "step_id": "step_2"},
                {"label": "Customer DELETE", "step_id": "step_3"},
            ],
        ),
        _step(
            title="Delete Inactive Customers",
            goal="Issue the proposed destructive cleanup only after the explicit gate.",
            understanding=(
                "This stage proposes the DELETE for the rows measured in the preview. It is a destructive "
                "write on the disposable per-run copy, so execution pauses at a breakpoint and the mandatory "
                "approval gate; no shortcut can execute it."
            ),
            target_data=[
                "Target: sandbox_customers (disposable SQLite)",
                "Predicate: latest order before the resolved cutoff",
                "Effect: DELETE each matching customer row",
                "Gate: breakpoint and mandatory approval",
            ],
            rationale="Permanent removal of customer records is a destructive operation and is never executed silently.",
            operations=[
                {
                    "tool": "sandbox_delete_inactive_customers",
                    "args": {
                        "table": "sandbox_customers",
                        "reference": "current_date",
                        "months": 12,
                        "include_never_ordered": True,
                    },
                }
            ],
            dependency_chain=[
                {"label": "Measured affected set", "step_id": "step_2"},
                {"label": "Customer DELETE", "step_id": "step_3"},
                {"label": "Verify", "step_id": "step_4"},
            ],
        ),
        _step(
            title="Verify and Report",
            goal="Re-read the sandbox after the decision and report what happened to customer records.",
            understanding=(
                "This final stage reads the sandbox again after the decision. It reports the source customer "
                "row count, the sandbox customer row count, how many rows are archived, and how many remain."
            ),
            target_data=[
                "Target: sandbox_customers",
                "Check: source rows vs sandbox rows; archived count",
                "Metric: customers_remaining",
                "Report: final cleanup state",
            ],
            rationale="Verification is a fresh read of the disposable copy and the immutable source count, not an assumed effect.",
            operations=[
                {
                    "tool": "sandbox_customer_audit",
                    "args": {
                        "table": "sandbox_customers",
                        "reference": "current_date",
                        "months": 12,
                        "include_never_ordered": True,
                    },
                },
                {
                    "tool": "compose_report",
                    "args": {
                        "title": "Customer inactivity cleanup",
                        "metric_names": ["customers_remaining"],
                        "summary": (
                            "Customer cleanup finished. The report records the customer rows remaining in "
                            "the disposable sandbox; archived rows remain present and are counted."
                        ),
                        "notes": ["The source CSVs are read-only; only the per-run SQLite copy can change."],
                    },
                },
            ],
            dependency_chain=[
                {"label": "Customer DELETE decision", "step_id": "step_3"},
                {"label": "Verified customer state", "step_id": "step_4"},
            ],
        ),
    ]
    return PlanDraft(
        scenario_id="customer_cleanup",
        reference_kind=None,
        task_spec={
            "objective": "Remove customers who have not placed an order in the last 12 months from a disposable customer table.",
            "population": "Rows in the per-run sandbox_customers table, joined to sandbox_orders by customer_id.",
            "time_scope": "The last 12 months relative to the current date at run time.",
            "metric_definitions": [inactive_metric],
            "semantic_bindings": [temporal_binding, target_binding, action_binding],
            "assumptions": [
                "The current date is the declared reference date for 'last 12 months' (to be tested against the data).",
                "Customers with no orders at all are included as inactive.",
                "A destructive DELETE must be approved explicitly before execution.",
            ],
        },
        steps=steps,
        planner_label="Scripted sandbox planner · prospective temporal cleanup with breakpoint and approval gate",
        initial_breakpoints=["Pause before DELETE or DROP operations."],
    )


def build_scripted_plan(task: str, dataset_name: str, dataset: OlistDataset) -> PlanDraft:
    if dataset_name == "sandbox":
        low = task.lower()
        customer_cleanup = "customer" in low and any(
            token in low
            for token in ("clean", "inactive", "not placed", "no order", "has not", "haven't", "12 month")
        )
        if customer_cleanup:
            return _customer_cleanup_plan(dataset)
        return _sandbox_plan(dataset)
    low = task.lower()
    if "repeat" in low or "purchase rate" in low or ("customer" in low and "rate" in low):
        return _repeat_purchase_plan(dataset)
    if "categor" in low or "sales" in low or "revenue" in low:
        return _category_sales_plan(dataset)
    raise ScenarioError(
        "Scripted Demo has two prepared Olist tasks: (A) customer repeat purchase rate and "
        "(B) monthly product sales by category. Use Live Agent for other tasks or one of the prepared tasks."
    )


def _find_group_evidence(run, step_index: int) -> dict[str, Any] | None:
    if step_index >= len(run.steps):
        return None
    for op in run.steps[step_index].operations:
        if op.tool != "group_and_aggregate":
            continue
        for ev in op.evidence:
            if ev.kind == "groupby":
                return ev.details
    return None


def _repeat_revision(run, step: Any, user_text: str, dataset: OlistDataset) -> dict[str, Any]:
    customers_schema = dataset.schema("customers", ["customer_id", "customer_unique_id"])
    orders_unique = int(dataset.table("orders")["customer_id"].nunique())
    customers_unique = int(dataset.table("customers")["customer_unique_id"].nunique())
    group_details = _find_group_evidence(run, 2)
    if group_details is None:
        delivered = dataset.table("orders")
        delivered = delivered[delivered["order_status"] == "delivered"]
        sizes = delivered.groupby("customer_id", dropna=False).size()
        groups_with_repeat = int((sizes >= 2).sum())
        groups = int(len(sizes))
    else:
        groups_with_repeat = int(group_details.get("groups_with_multiple_rows", 0))
        groups = int(group_details.get("output_groups", 0))
    unique_stats = dataset.table("customers")["customer_unique_id"].nunique()
    investigations = [
        Investigation(
            tool="inspect_schema",
            args={"table": "customers", "columns": ["customer_id", "customer_unique_id"]},
            summary="Read the documented semantics and cardinality of both customer identifiers.",
            facts=[
                Fact(label="customer_id distinct", value=fmt_int(customers_schema["columns"][0]["unique"])),
                Fact(label="customer_unique_id distinct", value=fmt_int(customers_schema["columns"][1]["unique"])),
                Fact(label="orders.customer_id distinct", value=fmt_int(orders_unique)),
            ],
        ),
        Investigation(
            tool="group_and_aggregate",
            args={"table": "included_orders", "by": ["customer_id"]},
            summary="Checked the current grouping grain recorded by the workflow.",
            facts=[
                Fact(label="Current groups", value=fmt_int(groups)),
                Fact(label="Groups with ≥2 orders", value=fmt_int(groups_with_repeat)),
                Fact(label="Persistent IDs available", value=fmt_int(customers_unique)),
            ],
        ),
    ]
    explanation = (
        "I checked the customer columns and the recorded grouping. The documented customers.customer_unique_id "
        "links records that belong to the same persistent customer, while orders.customer_id is one customer "
        "record per order. The current grouping produced "
        f"{groups_with_repeat:,} group(s) with at least two orders out of {groups:,}; "
        "that grain prevents repeat purchases from being observed. I will join orders to customers on customer_id "
        "and group by customer_unique_id instead. The metric definition itself is unchanged."
    )
    step3_id = run.steps[2].id
    step4_id = run.steps[3].id
    revised_definition = (
        "Number of persistent customers with at least 2 delivered orders divided by the number of persistent "
        "customers with at least 1 delivered order, using customers.customer_unique_id."
    )
    updates = [
        StepUpdate(
            step_id=step3_id,
            understanding=(
                "The evidence shows that orders.customer_id identifies order records rather than persistent "
                "customers. I will keep delivered orders, join customers on customer_id, and group by "
                "customer_unique_id so the requested rate counts customers rather than orders."
            ),
            target_data=[
                "Tables: orders, customers",
                "Join: orders.customer_id = customers.customer_id",
                "Key field: customer_unique_id (persistent customer identity)",
                "Output: one row per persistent customer with order_count",
            ],
            rationale="customer_id is an order-record key; customer_unique_id is the persistent identity required by the metric.",
            operations=[
                {
                    "tool": "filter_rows",
                    "args": {
                        "table": "orders",
                        "where": [{"column": "order_status", "op": "eq", "value": "delivered"}],
                        "output": "included_orders",
                    },
                },
                {
                    "tool": "join_tables",
                    "args": {
                        "left": "included_orders",
                        "right": "customers",
                        "left_on": "customer_id",
                        "right_on": "customer_id",
                        "how": "inner",
                        "output": "included_orders_customers",
                    },
                },
                {
                    "tool": "group_and_aggregate",
                    "args": {
                        "table": "included_orders_customers",
                        "by": ["customer_unique_id"],
                        "aggregations": [{"column": "order_id", "func": "count", "alias": "order_count"}],
                        "output": "customer_order_counts",
                    },
                },
            ],
        ),
        StepUpdate(
            step_id=step4_id,
            understanding=(
                "This stage applies the requested repeat-purchase definition to the corrected persistent-customer "
                "groups. The rate is the share of persistent customers with at least two delivered orders among "
                "those with at least one."
            ),
            operations=[
                {
                    "tool": "compute_metric",
                    "args": {
                        "table": "customer_order_counts",
                        "name": "repeat_purchase_rate",
                        "kind": "share_of_rows",
                        "where": [{"column": "order_count", "op": "gte", "value": 2}],
                        "unit": "percent",
                        "definition": revised_definition,
                        "numerator_label": "Repeat customers",
                        "denominator_label": "Persistent customers with an order",
                        "output": "metric_repeat_rate",
                    },
                }
            ],
        ),
    ]
    bindings = [b.model_copy(deep=True) for b in run.task_specs[-1].semantic_bindings]
    for binding in bindings:
        if binding.concept == "Customer Identity":
            binding.source_table = "customers"
            binding.column = "customer_unique_id"
            binding.status = "revised"
            binding.note = "Persistent customer identity. Requires orders→customers join on customer_id."
    metrics = [m.model_copy(deep=True) for m in run.task_specs[-1].metric_definitions]
    for metric in metrics:
        if metric.name == "repeat_purchase_rate":
            metric.definition = revised_definition
            metric.status = "confirmed"
    assumptions = [
        "Included order statuses = delivered.",
        "A persistent customer with at least two included orders is a repeat customer.",
        "Persistent customer identity = customers.customer_unique_id, reached from orders.customer_id.",
    ]
    changes = [
        RevisionChange(
            kind="binding",
            description="Customer Identity moves from the order-record key to the persistent customer key.",
            before="orders.customer_id",
            after="customers.customer_unique_id (via join on customer_id)",
        ),
        RevisionChange(
            kind="plan",
            description="Preparation adds an orders→customers join and groups by customer_unique_id.",
            before="filter delivered → group by customer_id",
            after="filter delivered → join customers → group by customer_unique_id",
        ),
        RevisionChange(
            kind="metric",
            description="The metric definition is made explicit over persistent customers.",
            before="share of order-record customer groups with ≥2 orders",
            after="share of persistent customers with ≥2 delivered orders",
        ),
    ]
    return {
        "explanation": explanation,
        "changes": changes,
        "proposed_bindings": bindings,
        "proposed_metrics": metrics,
        "proposed_assumptions": assumptions,
        "step_updates": updates,
        "investigations": investigations,
        "earliest_affected_step": 2,
        "status": "pending",
    }


def _category_revision(run, step: Any, user_text: str, dataset: OlistDataset) -> dict[str, Any]:
    from .references import reference_payment_diagnostics

    diagnostics = reference_payment_diagnostics(dataset)
    group_details = _find_group_evidence(run, 3)
    investigations = [
        Investigation(
            tool="inspect_schema",
            args={"table": "order_items", "columns": ["order_id", "order_item_id", "price", "freight_value"]},
            summary="Confirmed that item price is stored per item row and freight is a separate column.",
            facts=[
                Fact(label="Item rows", value=fmt_int(len(dataset.table("order_items")))),
                Fact(label="Freight column", value="freight_value (excluded from SUM(price))"),
            ],
        ),
        Investigation(
            tool="inspect_schema",
            args={"table": "order_payments", "columns": ["order_id", "payment_sequential", "payment_value"]},
            summary="Confirmed that payment_value is an order-level payment-record measure.",
            facts=[
                Fact(label="Payment records", value=fmt_int(len(dataset.table("order_payments")))),
                Fact(label="Identity", value="(order_id, payment_sequential)"),
            ],
        ),
        Investigation(
            tool="join_tables",
            args={"left": "order_lines", "right": "order_payments", "left_on": "order_id"},
            summary="Measured payment-record multiplicity at the item grain.",
            facts=[
                Fact(label="Item×payment rows", value=fmt_int(diagnostics["item_grain_payment_rows"])),
                Fact(label="Distinct payment total", value=diagnostics["formatted_distinct"]),
                Fact(label="Item-grain payment total", value=diagnostics["formatted_inflated"]),
            ],
        ),
    ]
    if group_details and group_details.get("payment_value_joined_sum"):
        old_total = group_details["payment_value_joined_sum"]
        old_total_text = fmt_currency(old_total)
    else:
        old_total_text = diagnostics["formatted_inflated"]
    explanation = (
        "I checked the payment and item grain. order_payments.payment_value is recorded once per payment record "
        "identified by (order_id, payment_sequential), while order_items.price is recorded once per item row. "
        "The current plan joins order-level payment records to multiple item rows and then sums payment_value, "
        f"so payment contributions repeat: the distinct delivered payment total is {diagnostics['formatted_distinct']}, "
        f"while the item-grain payment sum is {old_total_text}. I will use SUM(order_items.price), exclude freight, "
        "and remove order_payments from the plan. This changes the measure and data preparation, not just a label."
    )
    revised_definition = (
        "Monthly product sales by category = SUM(order_items.price), excluding freight_value, grouped by "
        "purchase month and product category for delivered orders."
    )
    step3_id = run.steps[2].id
    step4_id = run.steps[3].id
    updates = [
        StepUpdate(
            step_id=step3_id,
            understanding=(
                "The evidence shows that payment_value repeats when order-level payments are joined to item rows. "
                "I will keep delivered orders, attach item rows and category labels only, and prepare one row per "
                "item so SUM(price) measures sales without duplication."
            ),
            target_data=[
                "Tables: orders, order_items, products",
                "Measure field: order_items.price (per item row; freight excluded)",
                "Grain: one row per delivered order item",
                "Operations: filter delivered → join items → join products",
            ],
            rationale="Item price is already at the grain required for product/category sales and avoids order-level payment duplication.",
            operations=[
                {
                    "tool": "filter_rows",
                    "args": {
                        "table": "orders",
                        "where": [{"column": "order_status", "op": "eq", "value": "delivered"}],
                        "output": "included_orders",
                    },
                },
                {
                    "tool": "join_tables",
                    "args": {
                        "left": "included_orders",
                        "right": "order_items",
                        "left_on": "order_id",
                        "right_on": "order_id",
                        "how": "inner",
                        "output": "order_lines",
                    },
                },
                {
                    "tool": "join_tables",
                    "args": {
                        "left": "order_lines",
                        "right": "products",
                        "left_on": "product_id",
                        "right_on": "product_id",
                        "how": "left",
                        "output": "sales_detail",
                    },
                },
            ],
        ),
        StepUpdate(
            step_id=step4_id,
            understanding=(
                "This stage applies the requested month and category grouping to item-grain rows. Sales are the "
                "sum of item prices, excluding freight, so the total reflects the corrected measure."
            ),
            target_data=[
                "Input: sales_detail.price",
                "Group keys: purchase month, product_category_name",
                "Aggregation: SUM(price) AS sales",
                "Outputs: monthly_category_sales, total_sales",
            ],
            rationale="The correct measure is item price at item grain; payment values are not part of this plan.",
            operations=[
                {
                    "tool": "group_and_aggregate",
                    "args": {
                        "table": "sales_detail",
                        "by": ["product_category_name"],
                        "date_part": {"column": "order_purchase_timestamp", "alias": "month", "unit": "month"},
                        "aggregations": [{"column": "price", "func": "sum", "alias": "sales"}],
                        "output": "monthly_category_sales",
                    },
                },
                {
                    "tool": "compute_metric",
                    "args": {
                        "table": "monthly_category_sales",
                        "name": "total_sales",
                        "kind": "sum",
                        "column": "sales",
                        "unit": "currency",
                        "definition": revised_definition,
                        "output": "metric_total_sales",
                    },
                },
            ],
        ),
    ]
    bindings = [b.model_copy(deep=True) for b in run.task_specs[-1].semantic_bindings]
    for binding in bindings:
        if binding.concept == "Sales Measure":
            binding.source_table = "order_items"
            binding.column = "price"
            binding.status = "revised"
            binding.note = "Item price, excluding freight_value. Payment records are not used."
        elif binding.concept == "Sales Grain":
            binding.source_table = "order_items"
            binding.column = "order_id"
            binding.status = "revised"
            binding.note = "One row per item line, identified by (order_id, order_item_id)."
    metrics = [m.model_copy(deep=True) for m in run.task_specs[-1].metric_definitions]
    for metric in metrics:
        if metric.name == "total_sales":
            metric.definition = revised_definition
            metric.status = "confirmed"
    assumptions = [
        "Included order statuses = delivered.",
        "Product sales use order_items.price and exclude freight_value.",
        "Payment values are not an item-level sales measure for this task.",
    ]
    changes = [
        RevisionChange(
            kind="binding",
            description="The sales measure moves from order-level payments to item-level price.",
            before="order_payments.payment_value (payment-record grain)",
            after="order_items.price (item-row grain, freight excluded)",
        ),
        RevisionChange(
            kind="plan",
            description="The data preparation no longer joins order_payments.",
            before="orders → order_items → order_payments → products",
            after="orders → order_items → products",
        ),
        RevisionChange(
            kind="metric",
            description="The monthly category aggregation changes measure and grain.",
            before="SUM(payment_value) over item × payment rows",
            after="SUM(price) over delivered item rows",
        ),
    ]
    return {
        "explanation": explanation,
        "changes": changes,
        "proposed_bindings": bindings,
        "proposed_metrics": metrics,
        "proposed_assumptions": assumptions,
        "step_updates": updates,
        "investigations": investigations,
        "earliest_affected_step": 2,
        "status": "pending",
    }


def _customer_dataset_scope(
    dataset: OlistDataset, reference_mode: str, months: int = 12
) -> dict[str, Any]:
    customers = dataset.table("customers")
    orders = dataset.table("orders")
    timestamps = orders["order_purchase_timestamp"]
    first_order = timestamps.min()
    last_order = timestamps.max()
    latest_by_customer = orders.groupby("customer_id")["order_purchase_timestamp"].max()
    reference = last_order if reference_mode == "dataset_latest" else pd.Timestamp(date.today())
    cutoff = reference - pd.DateOffset(months=months)
    never_ordered = int((~customers["customer_id"].isin(latest_by_customer.index)).sum())
    inactive_with_orders = int((latest_by_customer < cutoff).sum())
    return {
        "total": int(len(customers)),
        "never_ordered": never_ordered,
        "inactive_with_orders": inactive_with_orders,
        "matching": inactive_with_orders + never_ordered,
        "first_order": first_order.strftime("%Y-%m-%d %H:%M:%S"),
        "last_order": last_order.strftime("%Y-%m-%d %H:%M:%S"),
        "reference": reference.strftime("%Y-%m-%d %H:%M:%S"),
        "cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _customer_preview_details(run) -> dict[str, Any] | None:
    for workflow_step in run.steps:
        for operation in workflow_step.operations:
            if operation.tool != "sandbox_customer_preview":
                continue
            for evidence in operation.evidence:
                if evidence.kind == "sandbox_customer_preview":
                    return evidence.details
    return None


def _customer_cleanup_revision(run, step: Any, user_text: str, dataset: OlistDataset) -> dict[str, Any]:
    details = _customer_preview_details(run) or {}
    current_scope = _customer_dataset_scope(dataset, "current_date")
    revised_scope = _customer_dataset_scope(dataset, "dataset_latest")
    total = int(details.get("customers_total", current_scope["total"]))
    never_ordered = int(details.get("never_ordered", current_scope["never_ordered"]))
    current_matching = int(details.get("matching_customers", current_scope["matching"]))
    current_cutoff = str(details.get("cutoff", current_scope["cutoff"]))
    dataset_last_order = str(details.get("dataset_last_order", revised_scope["last_order"]))
    revised_cutoff = revised_scope["cutoff"]
    revised_matching = revised_scope["matching"]

    explanation = (
        "I re-read the recorded preview and the actual order range. The current-date interpretation "
        f"resolves to a cutoff of {current_cutoff}: {fmt_int(current_matching)} of {fmt_int(total)} "
        f"customer rows match, because the dataset's latest order is {dataset_last_order}. Customers "
        f"with no orders ({fmt_int(never_ordered)}) are currently included. I will anchor the reference "
        f"to the latest order in the historical dataset (cutoff {revised_cutoff}), under which "
        f"{fmt_int(revised_matching)} customer rows are inactive, and replace the DELETE with a "
        "non-destructive archival update on the isolated copy. No customer records are removed."
    )
    step2_id = run.steps[1].id
    step3_id = run.steps[2].id
    step4_id = run.steps[3].id
    preview_args = {
        "table": "sandbox_customers",
        "reference": "dataset_latest",
        "months": 12,
        "include_never_ordered": True,
    }
    updates = [
        StepUpdate(
            step_id=step2_id,
            understanding=(
                "This stage re-runs the read-only preview with the corrected temporal reference. The "
                "reference is now the latest order date in the historical dataset, not the current date, "
                "and the 12-month window is measured backwards from it."
            ),
            target_data=[
                "Reference: dataset latest order",
                f"Cutoff: {revised_cutoff}",
                "Predicate: latest order before cutoff; no-order customers included",
                "Effect: none (read-only preview)",
            ],
            rationale="The developer defined inactivity relative to the historical dataset's own latest order date.",
            operations=[{"tool": "sandbox_customer_preview", "args": dict(preview_args)}],
        ),
        StepUpdate(
            step_id=step3_id,
            title="Archive Inactive Customers",
            goal="Mark the measured inactive customers as archived without removing any row.",
            understanding=(
                "This stage replaces the proposed DELETE with an archival update on the disposable copy. "
                "It sets archived = 1 for the customers measured under the dataset-latest reference and "
                "leaves every customer record present."
            ),
            target_data=[
                "Target: sandbox_customers (disposable SQLite)",
                "Predicate: latest order before the dataset-latest cutoff",
                "Effect: UPDATE archived = 1; no rows removed",
                "Retention: source customer records remain intact",
            ],
            rationale="Archiving preserves the historical customer records while still marking the inactive set.",
            operations=[{"tool": "sandbox_archive_inactive_customers", "args": dict(preview_args)}],
        ),
        StepUpdate(
            step_id=step4_id,
            understanding=(
                "This stage verifies the revised affected set and the non-destructive result. It re-reads "
                "the source and sandbox customer row counts, the archived count, and the active count."
            ),
            target_data=[
                "Target: sandbox_customers",
                "Check: source rows = sandbox rows; archived count",
                "Metrics: customers_archived, customers_remaining",
                "Report: revised cleanup state with source records intact",
            ],
            operations=[
                {"tool": "sandbox_customer_audit", "args": dict(preview_args)},
                {
                    "tool": "compose_report",
                    "args": {
                        "title": "Customer inactivity cleanup (archived)",
                        "metric_names": ["customers_archived", "customers_remaining"],
                        "summary": (
                            "Inactive customers were archived by setting archived = 1. No customer "
                            "records were removed, and the source and sandbox customer row counts agree."
                        ),
                        "notes": [
                            f"Reference: latest dataset order; cutoff {revised_cutoff}.",
                            "The source CSVs are read-only; only the per-run SQLite copy was updated.",
                        ],
                    },
                },
            ],
        ),
    ]
    bindings = [b.model_copy(deep=True) for b in run.task_specs[-1].semantic_bindings]
    for binding in bindings:
        if binding.concept == "Temporal Reference":
            binding.status = "revised"
            binding.note = (
                f"12 months before the dataset's latest order ({dataset_last_order}); "
                f"cutoff {revised_cutoff}."
            )
        elif binding.concept == "Cleanup Target":
            binding.status = "confirmed"
        elif binding.concept == "Cleanup Action":
            binding.column = "archived"
            binding.status = "revised"
            binding.note = "Set archived = 1 on matching rows; no customer records are removed."
    metrics = [m.model_copy(deep=True) for m in run.task_specs[-1].metric_definitions]
    for metric in metrics:
        if metric.name == "inactive_customers":
            metric.definition = (
                "Customer rows whose latest order predates the cutoff 12 months before the dataset's "
                "latest order; customers with no orders are included."
            )
            metric.status = "confirmed"
    if not any(metric.name == "customers_archived" for metric in metrics):
        metrics.append(
            MetricDefinition(
                name="customers_archived",
                definition=(
                    "Customer rows marked archived = 1 in the disposable sandbox after the "
                    "non-destructive inactivity cleanup."
                ),
                unit="number",
                status="confirmed",
            )
        )
    if not any(metric.name == "customers_remaining" for metric in metrics):
        metrics.append(
            MetricDefinition(
                name="customers_remaining",
                definition=(
                    "Customer rows remaining in the disposable sandbox after the cleanup decision; "
                    "archived rows remain present and are counted."
                ),
                unit="number",
                status="confirmed",
            )
        )
    assumptions = [
        "Inactivity is evaluated relative to the latest order date in the historical dataset.",
        "The 12-month window is measured backwards from that dataset-latest reference date.",
        "Customers with no orders are included as inactive.",
        "The cleanup archives matching rows; it does not delete source customer records.",
    ]
    changes = [
        RevisionChange(
            kind="binding",
            description="The temporal reference moves from the current date to the dataset's latest order.",
            before=f"current date, cutoff {current_cutoff}",
            after=f"dataset latest order ({dataset_last_order}), cutoff {revised_cutoff}",
        ),
        RevisionChange(
            kind="binding",
            description="The cleanup action moves from permanent row deletion to an archival flag.",
            before="sandbox_customers.customer_id (DELETE)",
            after="sandbox_customers.archived = 1 (UPDATE; no rows removed)",
        ),
        RevisionChange(
            kind="plan",
            description="The preview, write, and verification operations are rewritten for the revision.",
            before="preview current date → DELETE matching rows → audit",
            after="preview dataset latest → UPDATE archived flag → audit source and archived counts",
        ),
        RevisionChange(
            kind="assumption",
            description="The no-order policy and retention behavior are made explicit.",
            before=" ".join(run.task_specs[-1].assumptions),
            after=" ".join(assumptions),
        ),
    ]
    investigations = [
        Investigation(
            tool="sandbox_customer_preview",
            args={
                "table": "sandbox_customers",
                "reference": "current_date",
                "months": 12,
                "include_never_ordered": True,
            },
            summary="Re-read the recorded current-date preview against the actual sandbox.",
            facts=[
                Fact(label="Declared reference", value=current_cutoff),
                Fact(label="Matching customers", value=fmt_int(current_matching)),
                Fact(label="Dataset latest order", value=dataset_last_order),
                Fact(label="Never ordered", value=fmt_int(never_ordered)),
            ],
        ),
        Investigation(
            tool="sandbox_customer_audit",
            args={
                "table": "sandbox_customers",
                "reference": "dataset_latest",
                "months": 12,
                "include_never_ordered": True,
            },
            summary="Measured the revised affected set against the historical order range.",
            facts=[
                Fact(label="Dataset-latest cutoff", value=revised_cutoff),
                Fact(label="Inactive customers", value=fmt_int(revised_matching)),
                Fact(label="Customer rows", value=fmt_int(total)),
                Fact(label="Rows removed by revision", value="0"),
            ],
        ),
    ]
    return {
        "explanation": explanation,
        "changes": changes,
        "proposed_bindings": bindings,
        "proposed_metrics": metrics,
        "proposed_assumptions": assumptions,
        "step_updates": updates,
        "investigations": investigations,
        "earliest_affected_step": 1,
        "status": "pending",
    }


def build_scripted_revision(run, step: Any, user_text: str, dataset: OlistDataset) -> dict[str, Any]:
    if run.scenario_id == "repeat_purchase":
        return _repeat_revision(run, step, user_text, dataset)
    if run.scenario_id == "category_sales":
        return _category_revision(run, step, user_text, dataset)
    if run.scenario_id == "customer_cleanup":
        return _customer_cleanup_revision(run, step, user_text, dataset)
    raise ScenarioError(
        "This scripted checkpoint has no prepared semantic revision. Destructive sandbox operations use "
        "the explicit Approve / Reject gate instead of a plan revision."
    )
