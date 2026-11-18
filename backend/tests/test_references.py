from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from backend.app.models import MetricValue, Report
from backend.app.references import (
    _category_sales_metric,
    _grouped_sales_table,
    reference_category_sales,
    reference_payment_diagnostics,
    reference_repeat_purchase_rate,
)


def test_grouped_sales_table_accepts_live_column_aliases():
    frame = pd.DataFrame(
        {
            "order_month": ["2024-01", "2024-01"],
            "product_category_name": ["toys", "books"],
            "category_sales": [10.0, 20.0],
        }
    )
    grouped = _grouped_sales_table(frame)
    assert list(grouped.columns) == ["month", "product_category_name", "sales"]
    assert grouped["sales"].sum() == 30.0


def test_category_sales_metric_resolves_a_live_total_name():
    metric = MetricValue(
        name="total_delivered_item_sales",
        value=13221498.11,
        formatted="R$ 13,221,498.11",
        unit="currency",
    )
    run = SimpleNamespace(
        metrics={metric.name: metric},
        report=Report(title="Monthly sales", summary="Reported.", metrics=[metric]),
    )
    assert _category_sales_metric(run) is metric


def test_repeat_purchase_reference_uses_persistent_identity(dataset):
    reference = reference_repeat_purchase_rate(dataset)
    assert reference["included_orders"] == 96478
    assert reference["persistent_customers"] == 93358
    assert reference["repeat_customers"] == 2801
    assert reference["formatted"] == "3.0003%"
    # The diagnostic order-record key cannot observe repeats at all.
    assert reference["order_record_key_groups"] == 96478
    assert reference["order_record_key_repeat_groups"] == 0


def test_category_sales_reference_and_payment_inflation(dataset):
    reference = reference_category_sales(dataset)
    assert reference["included_orders"] == 96478
    assert reference["item_rows"] == 110197
    assert reference["output_rows"] == 1273
    assert reference["months"] == 23
    assert reference["formatted"] == "R$ 13,221,498.11"

    diagnostics = reference_payment_diagnostics(dataset)
    assert diagnostics["delivered_payment_records"] == 100756
    assert diagnostics["item_grain_payment_rows"] == 115035
    assert diagnostics["formatted_distinct"] == "R$ 15,422,461.77"
    assert diagnostics["formatted_inflated"] == "R$ 19,776,160.44"
    assert round(diagnostics["inflation_ratio"], 4) == 1.2823
