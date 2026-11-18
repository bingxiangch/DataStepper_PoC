#!/usr/bin/env python3
"""Print the authoritative data facts used by the paper demo and tests.

The reference implementations read the raw CSVs independently from the agent's
tool workflow.  Numbers printed here are measured, never hardcoded projections.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.dataset import OlistDataset  # noqa: E402
from backend.app.references import (  # noqa: E402
    reference_category_sales,
    reference_payment_diagnostics,
    reference_repeat_purchase_rate,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit one JSON object instead of a readable report.")
    args = parser.parse_args()

    dataset = OlistDataset()
    if not dataset.available:
        print(
            f"Olist data is not available at {dataset.data_dir}. Run scripts/prepare_data.py first.",
            file=sys.stderr,
        )
        return 1

    orders = dataset.table("orders")
    customers = dataset.table("customers")
    repeat = reference_repeat_purchase_rate(dataset)
    category = reference_category_sales(dataset)
    payments = reference_payment_diagnostics(dataset)
    category_facts = {key: value for key, value in category.items() if key != "table"}

    timestamps = orders["order_purchase_timestamp"]
    latest_by_customer = orders.groupby("customer_id")["order_purchase_timestamp"].max()
    never_ordered = int((~customers["customer_id"].isin(latest_by_customer.index)).sum())
    last_order = timestamps.max()
    current_cutoff = pd.Timestamp(date.today()) - pd.DateOffset(months=12)
    dataset_cutoff = last_order - pd.DateOffset(months=12)
    customer_cleanup = {
        "first_order": timestamps.min().strftime("%Y-%m-%d %H:%M:%S"),
        "last_order": last_order.strftime("%Y-%m-%d %H:%M:%S"),
        "never_ordered": never_ordered,
        "current_date": date.today().isoformat(),
        "current_cutoff": current_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        "inactive_current_date": int((latest_by_customer < current_cutoff).sum()) + never_ordered,
        "dataset_latest_cutoff": dataset_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        "inactive_dataset_latest": int((latest_by_customer < dataset_cutoff).sum()) + never_ordered,
    }

    facts = {
        "dataset": {
            "directory": str(dataset.data_dir),
            "orders": int(len(orders)),
            "delivered_orders": int((orders["order_status"] == "delivered").sum()),
            "canceled_orders": int((orders["order_status"] == "canceled").sum()),
            "customer_rows": int(len(customers)),
            "orders_customer_id_distinct": int(orders["customer_id"].nunique()),
            "customers_customer_unique_id_distinct": int(customers["customer_unique_id"].nunique()),
        },
        "repeat_purchase_reference": repeat,
        "category_sales_reference": category_facts,
        "payment_grain_diagnostics": payments,
        "customer_inactivity": customer_cleanup,
    }

    if args.json:
        print(json.dumps(facts, indent=2, default=str))
        return 0

    print("Olist data facts (computed from raw CSVs)\n")
    print(f"Data directory: {dataset.data_dir}")
    print(f"Orders: {len(orders):,}  |  delivered: {facts['dataset']['delivered_orders']:,}"
          f"  |  canceled: {facts['dataset']['canceled_orders']:,}")
    print(f"customers rows: {len(customers):,}")
    print(f"orders.customer_id distinct: {facts['dataset']['orders_customer_id_distinct']:,}")
    print(
        "customers.customer_unique_id distinct: "
        f"{facts['dataset']['customers_customer_unique_id_distinct']:,}"
    )

    print("\nCase A — repeat purchase reference")
    print(f"  Included delivered orders: {repeat['included_orders']:,}")
    print(f"  Persistent customers: {repeat['persistent_customers']:,}")
    print(f"  Repeat customers: {repeat['repeat_customers']:,}")
    print(f"  Repeat purchase rate: {repeat['formatted']}")
    print(
        "  Diagnostic order-record grouping: "
        f"{repeat['order_record_key_groups']:,} groups, "
        f"{repeat['order_record_key_repeat_groups']:,} repeat groups"
    )

    print("\nCase B — category sales reference")
    print(f"  Included delivered orders: {category['included_orders']:,}")
    print(f"  Joined item rows: {category['item_rows']:,}")
    print(f"  Month × category groups: {category['output_rows']:,} across {category['months']} months")
    print(f"  Total sales (order_items.price): {category['formatted']}")

    print("\nCase B — payment-grain diagnostics")
    print(f"  Delivered payment records: {payments['delivered_payment_records']:,}")
    print(f"  Rows after item-grain payment join: {payments['item_grain_payment_rows']:,}")
    print(f"  Distinct delivered payment total: {payments['formatted_distinct']}")
    print(f"  Item-grain payment total: {payments['formatted_inflated']}")
    print(f"  Inflation ratio: {payments['inflation_ratio']:.4f}×")

    cleanup = customer_cleanup
    print("\nCase C — customer inactivity")
    print(f"  Order range: {cleanup['first_order']} → {cleanup['last_order']}")
    print(f"  Customers never ordering: {cleanup['never_ordered']:,} (included)")
    print(
        f"  Current date {cleanup['current_date']} → cutoff {cleanup['current_cutoff']}: "
        f"{cleanup['inactive_current_date']:,} inactive customer rows"
    )
    print(
        f"  Dataset-latest cutoff {cleanup['dataset_latest_cutoff']}: "
        f"{cleanup['inactive_dataset_latest']:,} inactive customer rows"
    )
    print("\nThese are measured facts. They are not produced by the demo UI or hardcoded in tool outcomes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
