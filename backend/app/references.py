"""Independent reference computations.

These functions deliberately do not call the agent workflow, its tools, or its
artifacts.  They are separate implementations over the raw Olist tables, used
to sanity-check replayed results.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .dataset import OlistDataset


def reference_repeat_purchase_rate(dataset: OlistDataset) -> dict[str, Any]:
    orders = dataset.table("orders")
    customers = dataset.table("customers")

    included = orders.loc[orders["order_status"] == "delivered", ["order_id", "customer_id"]].copy()
    persistent = customers.loc[:, ["customer_id", "customer_unique_id"]]
    joined = included.merge(persistent, on="customer_id", how="left", validate="many_to_one")
    if joined["customer_unique_id"].isna().any():
        raise RuntimeError("Reference check failed: a delivered order has no persistent customer identifier.")
    per_customer = joined.groupby("customer_unique_id", dropna=False).size()
    repeat_customers = int((per_customer >= 2).sum())
    customers_with_order = int(len(per_customer))
    rate = repeat_customers / customers_with_order if customers_with_order else None

    # Diagnostic only: the same calculation using the order-record key.
    order_record_counts = included.groupby("customer_id", dropna=False).size()
    order_record_repeat = int((order_record_counts >= 2).sum())

    return {
        "definition": (
            "Repeat purchase rate = persistent customers with >= 2 delivered orders / "
            "persistent customers with >= 1 delivered order. Status = delivered; "
            "time scope = all order purchase timestamps."
        ),
        "included_orders": int(len(included)),
        "persistent_customers": customers_with_order,
        "repeat_customers": repeat_customers,
        "rate": rate,
        "formatted": f"{rate * 100:.4f}%" if rate is not None else "—",
        "order_record_key_repeat_groups": order_record_repeat,
        "order_record_key_groups": int(len(order_record_counts)),
    }


def reference_category_sales(dataset: OlistDataset) -> dict[str, Any]:
    orders = dataset.table("orders")
    items = dataset.table("order_items")
    products = dataset.table("products")

    included = orders.loc[
        orders["order_status"] == "delivered",
        ["order_id", "order_purchase_timestamp"],
    ].copy()
    item_lines = items.loc[:, ["order_id", "product_id", "price", "freight_value"]].copy()
    joined = included.merge(item_lines, on="order_id", how="inner", validate="one_to_many")
    product_categories = products.loc[:, ["product_id", "product_category_name"]].copy()
    product_categories["product_category_name"] = product_categories["product_category_name"].fillna("<unknown>")
    joined = joined.merge(product_categories, on="product_id", how="left", validate="many_to_one")
    joined["product_category_name"] = joined["product_category_name"].fillna("<unknown>")
    joined["month"] = pd.to_datetime(joined["order_purchase_timestamp"], errors="coerce").dt.to_period("M").astype("string")
    grouped = (
        joined.groupby(["month", "product_category_name"], dropna=False)
        .agg(sales=("price", "sum"), item_rows=("price", "size"))
        .reset_index()
    )
    total = float(grouped["sales"].sum()) if len(grouped) else 0.0
    return {
        "definition": (
            "Product sales = SUM(order_items.price), excluding freight_value, by purchase month "
            "and product category, for delivered orders across all purchase timestamps."
        ),
        "included_orders": int(len(included)),
        "item_rows": int(len(joined)),
        "output_rows": int(len(grouped)),
        "months": int(grouped["month"].nunique()),
        "total_sales": total,
        "formatted": f"R$ {total:,.2f}",
        "table": grouped,
    }


def reference_payment_diagnostics(dataset: OlistDataset) -> dict[str, Any]:
    """Diagnostic references for the problematic payment-based formulation."""
    orders = dataset.table("orders")
    items = dataset.table("order_items")
    payments = dataset.table("order_payments")

    delivered = orders.loc[orders["order_status"] == "delivered", ["order_id"]].copy()
    delivered_payments = delivered.merge(payments, on="order_id", how="inner", validate="one_to_many")
    distinct_total = float(delivered_payments["payment_value"].sum())
    delivered_items = delivered.merge(items.loc[:, ["order_id", "price"]], on="order_id", how="inner", validate="one_to_many")
    inflated = delivered_items.merge(
        payments.loc[:, ["order_id", "payment_sequential", "payment_value"]],
        on="order_id",
        how="inner",
        validate="many_to_many",
    )
    inflated_total = float(inflated["payment_value"].sum())
    return {
        "delivered_payment_records": int(len(delivered_payments)),
        "item_grain_payment_rows": int(len(inflated)),
        "distinct_delivered_payment_total": distinct_total,
        "item_grain_payment_total": inflated_total,
        "inflation_ratio": inflated_total / distinct_total if distinct_total else None,
        "formatted_distinct": f"R$ {distinct_total:,.2f}",
        "formatted_inflated": f"R$ {inflated_total:,.2f}",
    }


def _normalise_category(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna("<unknown>").replace({"": "<unknown>"})


def _find_column(frame: pd.DataFrame, candidates: list[str], contains: str) -> str | None:
    lowered = {str(column).lower(): column for column in frame.columns}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    matches = [column for column in frame.columns if contains in str(column).lower()]
    return matches[0] if len(matches) == 1 else None


def _grouped_sales_table(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    month_column = _find_column(frame, ["month", "order_month", "purchase_month"], "month")
    category_column = _find_column(
        frame, ["product_category_name", "category", "product_category"], "categor"
    )
    sales_column = _find_column(
        frame,
        ["sales", "category_sales", "total_sales", "monthly_category_sales"],
        "sales",
    )
    if month_column is None:
        raise ValueError(f"monthly sales artifact has no month column: {list(frame.columns)}")
    if category_column is None:
        raise ValueError(f"monthly sales artifact has no category column: {list(frame.columns)}")
    if sales_column is None:
        numeric = [
            column
            for column in frame.columns
            if column not in {month_column, category_column}
            and pd.api.types.is_numeric_dtype(frame[column])
        ]
        if len(numeric) == 1:
            sales_column = numeric[0]
    if sales_column is None:
        raise ValueError(f"monthly sales artifact has no sales column: {list(frame.columns)}")
    frame["month"] = frame[month_column].astype("string")
    frame["product_category_name"] = _normalise_category(frame[category_column])
    frame["sales"] = pd.to_numeric(frame[sales_column], errors="coerce")
    return (
        frame.groupby(["month", "product_category_name"], dropna=False)["sales"]
        .sum()
        .reset_index()
    )


def _category_sales_metric(run):
    """Find the scalar total that a category-sales report actually uses.

    Live planners may choose a descriptive metric name such as
    ``total_delivered_item_sales`` instead of the scripted ``total_sales``.
    Prefer the exact scripted name, then a unique currency metric in the
    report, then a unique currency metric in the run.
    """
    exact = run.metrics.get("total_sales")
    if exact is not None:
        return exact
    report_metrics = [
        metric
        for metric in (run.report.metrics if run.report is not None else [])
        if metric.unit == "currency"
    ]
    if len(report_metrics) == 1:
        return report_metrics[0]
    currency_metrics = [metric for metric in run.metrics.values() if metric.unit == "currency"]
    if len(currency_metrics) == 1:
        return currency_metrics[0]
    named = [
        metric
        for metric in run.metrics.values()
        if "total" in metric.name.lower() and "sales" in metric.name.lower()
    ]
    return named[0] if len(named) == 1 else None


def reference_check_for_run(run, store, dataset: OlistDataset) -> dict[str, Any] | None:
    kind = run.reference_kind or run.scenario_id
    if kind == "repeat_purchase":
        reference = reference_repeat_purchase_rate(dataset)
        metric = run.metrics.get("repeat_purchase_rate")
        value = metric.value if metric else None
        difference = None if value is None or reference["rate"] is None else value - reference["rate"]
        return {
            "kind": "repeat_purchase",
            "definition": reference["definition"],
            "workflow_value": value,
            "workflow_formatted": metric.formatted if metric else "—",
            "reference_value": reference["rate"],
            "reference_formatted": reference["formatted"],
            "difference": difference,
            "agreement": difference is not None and abs(difference) < 1e-12,
            "numerator": reference["repeat_customers"],
            "denominator": reference["persistent_customers"],
            "diagnostics": {
                "order_record_key_groups": reference["order_record_key_groups"],
                "order_record_key_repeat_groups": reference["order_record_key_repeat_groups"],
            },
        }
    if kind == "category_sales":
        reference = reference_category_sales(dataset)
        metric = _category_sales_metric(run)
        value = metric.value if metric else None
        difference = None if value is None else value - reference["total_sales"]
        grouped_agreement = None
        max_group_diff = None
        mismatched_groups = None
        table_name = run.report.table_name if run.report is not None else None
        artifact_id = run.tables.get(table_name or "monthly_category_sales")
        if artifact_id:
            try:
                workflow_table = _grouped_sales_table(store.get_frame(artifact_id, dataset))
                reference_table = _grouped_sales_table(reference["table"])
                merged = workflow_table.merge(
                    reference_table,
                    on=["month", "product_category_name"],
                    how="outer",
                    suffixes=("_workflow", "_reference"),
                )
                merged["difference"] = merged["sales_workflow"].fillna(0) - merged["sales_reference"].fillna(0)
                max_group_diff = float(merged["difference"].abs().max()) if len(merged) else 0.0
                mismatched_groups = int((merged["difference"].abs() > 0.01).sum())
                grouped_agreement = mismatched_groups == 0
            except Exception as exc:  # explicit evidence of a comparison problem
                grouped_agreement = False
                max_group_diff = None
                mismatched_groups = None
                comparison_error = str(exc)
            else:
                comparison_error = None
        else:
            comparison_error = None
        return {
            "kind": "category_sales",
            "definition": reference["definition"],
            "workflow_value": value,
            "workflow_formatted": metric.formatted if metric else "—",
            "reference_value": reference["total_sales"],
            "reference_formatted": reference["formatted"],
            "difference": difference,
            "agreement": difference is not None and abs(difference) < 0.01 and grouped_agreement is True,
            "grouped_agreement": grouped_agreement,
            "max_group_difference": max_group_diff,
            "mismatched_groups": mismatched_groups,
            "comparison_error": comparison_error,
            "num_groups": reference["output_rows"],
            "diagnostics": reference_payment_diagnostics(dataset),
        }
    return None
