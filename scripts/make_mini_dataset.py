#!/usr/bin/env python3
"""Build a small, schema-compatible Olist sample for fast testing.

The sample is copied from the real Olist CSVs, not synthesized. It keeps the
two failure shapes the demo depends on:

* two persistent customers have multiple delivered orders, so grouping by
  ``orders.customer_id`` reports 0% while ``customers.customer_unique_id``
  exposes repeat purchases;
* at least one delivered order has multiple item/payment rows, so joining
  payments at item grain inflates ``payment_value``.

It also includes canceled orders so the sandbox approval demo has rows to
delete.  This dataset is for exercising the UI/runtime quickly.  The
authoritative demo values in the README and paper refer to the full Olist data.

Usage:
    python scripts/make_mini_dataset.py
    python scripts/make_mini_dataset.py --source data/olist --dest data/olist_mini --force
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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

FILES = {
    "orders": "olist_orders_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "products": "olist_products_dataset.csv",
    "translation": "product_category_name_translation.csv",
}
REQUIRED_COLUMNS = {
    "orders": ["order_id", "customer_id", "order_status", "order_purchase_timestamp"],
    "customers": ["customer_id", "customer_unique_id"],
    "items": ["order_id", "order_item_id", "product_id", "price", "freight_value"],
    "payments": ["order_id", "payment_sequential", "payment_value"],
    "products": ["product_id", "product_category_name"],
    "translation": ["product_category_name", "product_category_name_english"],
}
TOTAL_ORDERS = 10
DELIVERED_REPEAT_ORDERS = (3, 2)
SINGLE_ORDERS = 3
CANCELED_ORDERS = 2


def _read(source: Path) -> dict[str, pd.DataFrame]:
    missing = [name for name in FILES.values() if not (source / name).is_file()]
    if missing:
        raise SystemExit(
            "Source is missing these files:\n  "
            + "\n  ".join(missing)
            + f"\nSource looked in: {source}"
        )
    tables: dict[str, pd.DataFrame] = {}
    for key, filename in FILES.items():
        encoding = "utf-8-sig" if key == "translation" else "utf-8"
        frame = pd.read_csv(source / filename, encoding=encoding)
        absent = [column for column in REQUIRED_COLUMNS[key] if column not in frame.columns]
        if absent:
            raise SystemExit(f"{filename} is missing columns: {', '.join(absent)}")
        tables[key] = frame
    return tables


def _row_counts(orders: pd.DataFrame, items: pd.DataFrame, payments: pd.DataFrame) -> pd.DataFrame:
    items_per_order = items.groupby("order_id").size()
    payments_per_order = payments.groupby("order_id").size()
    enriched = orders.copy()
    enriched["_ts"] = pd.to_datetime(enriched["order_purchase_timestamp"], errors="coerce")
    enriched["_items"] = enriched["order_id"].map(items_per_order).fillna(0).astype(int)
    enriched["_pays"] = enriched["order_id"].map(payments_per_order).fillna(0).astype(int)
    return enriched


def _pick_repeat_orders(delivered: pd.DataFrame) -> tuple[str, list[str], str]:
    """Pick one 3-order and one 2-order persistent customer.

    The second customer must own an order that duplicates payments at item
    grain; otherwise the category-sales failure would disappear.
    """
    candidates: list[tuple[int, str, list[str]]] = []
    for customer_unique_id, group in delivered.groupby("customer_unique_id", sort=True):
        if len(group) < 3:
            continue
        if (group["_items"] < 1).any() or (group["_pays"] < 1).any():
            continue
        picked = group.sort_values(["_ts", "order_id"]).head(3)
        cost = int(picked["_items"].sum() + picked["_pays"].sum())
        candidates.append((cost, str(customer_unique_id), picked["order_id"].tolist()))
    if not candidates:
        raise SystemExit("No persistent customer with three complete delivered orders was found.")
    candidates.sort(key=lambda item: (item[0], item[1]))
    first_cost, first_uid, first_orders = candidates[0]

    second: list[tuple[int, str, list[str]]] = []
    for customer_unique_id, group in delivered.groupby("customer_unique_id", sort=True):
        if str(customer_unique_id) == first_uid or len(group) < 2:
            continue
        complete = group[(group["_items"] > 0) & (group["_pays"] > 0)]
        if len(complete) < 2:
            continue
        multi = complete[(complete["_items"] > 1) | (complete["_pays"] > 1)]
        if multi.empty:
            continue
        anchor = multi.sort_values(["_items", "_pays", "_ts", "order_id"]).iloc[0]
        other = complete[complete["order_id"] != anchor["order_id"]].sort_values(["_ts", "order_id"]).iloc[0]
        cost = int(anchor["_items"] + anchor["_pays"] + other["_items"] + other["_pays"])
        second.append((cost, str(customer_unique_id), [str(anchor["order_id"]), str(other["order_id"])]))
    if not second:
        raise SystemExit("No second persistent customer with a multi-row payment/item order was found.")
    second.sort(key=lambda item: (item[0], item[1]))
    _, second_uid, second_orders = second[0]

    return first_uid, first_orders + second_orders, second_uid


def _pick_single_orders(delivered: pd.DataFrame, used: set[str]) -> list[str]:
    candidates = delivered[
        (~delivered["customer_unique_id"].isin(used))
        & (delivered["_items"] > 0)
        & (delivered["_pays"] > 0)
    ].copy()
    candidates["_size"] = candidates["_items"] + candidates["_pays"]
    candidates = candidates.sort_values(["_size", "_ts", "order_id"])
    picked: list[str] = []
    seen: set[str] = set()
    for _, row in candidates.iterrows():
        uid = str(row["customer_unique_id"])
        if uid in seen:
            continue
        seen.add(uid)
        picked.append(str(row["order_id"]))
        if len(picked) == SINGLE_ORDERS:
            break
    if len(picked) < SINGLE_ORDERS:
        raise SystemExit(f"Could not find {SINGLE_ORDERS} distinct single-order customers.")
    return picked


def _pick_canceled_orders(orders: pd.DataFrame, customers: pd.DataFrame, used: set[str]) -> list[str]:
    merged = orders.merge(
        customers[["customer_id", "customer_unique_id"]],
        on="customer_id",
        how="left",
        validate="many_to_one",
    )
    enriched = merged[
        (merged["order_status"] == "canceled")
        & merged["customer_unique_id"].notna()
        & (~merged["customer_unique_id"].astype(str).isin(used))
    ].copy()
    enriched["_size"] = enriched["_items"] + enriched["_pays"]
    enriched = enriched.sort_values(["_size", "_ts", "order_id"])
    picked: list[str] = []
    seen: set[str] = set()
    for _, row in enriched.iterrows():
        uid = str(row["customer_unique_id"])
        if uid in seen:
            continue
        seen.add(uid)
        picked.append(str(row["order_id"]))
        if len(picked) == CANCELED_ORDERS:
            break
    if len(picked) < CANCELED_ORDERS:
        raise SystemExit(f"Could not find {CANCELED_ORDERS} canceled orders with customer records.")
    return picked


def _select(tables: dict[str, pd.DataFrame]) -> tuple[list[str], dict[str, str]]:
    orders = _row_counts(tables["orders"], tables["items"], tables["payments"])
    customers = tables["customers"]
    delivered = orders[orders["order_status"] == "delivered"].merge(
        customers[["customer_id", "customer_unique_id"]],
        on="customer_id",
        how="left",
        validate="many_to_one",
    )
    if delivered["customer_unique_id"].isna().any():
        raise SystemExit("A delivered order has no matching customer record.")

    first_uid, repeat_orders, second_uid = _pick_repeat_orders(delivered)
    used = {first_uid, second_uid}
    singles = _pick_single_orders(delivered, used)
    used.update(
        delivered.loc[delivered["order_id"].isin(singles), "customer_unique_id"].astype(str)
    )
    canceled = _pick_canceled_orders(orders, customers, used)
    selected = repeat_orders + singles + canceled
    if len(selected) != TOTAL_ORDERS or len(set(selected)) != TOTAL_ORDERS:
        raise SystemExit(f"Expected {TOTAL_ORDERS} distinct orders, got {len(set(selected))}.")
    return selected, {
        "repeat_customer_a": first_uid,
        "repeat_customer_b": second_uid,
    }


def _write(tables: dict[str, pd.DataFrame], selected: list[str], dest: Path) -> dict[str, int]:
    orders = tables["orders"]
    customers = tables["customers"]
    items = tables["items"]
    payments = tables["payments"]
    products = tables["products"]

    selected_orders = orders[orders["order_id"].isin(selected)].copy()
    selected_orders["_ts"] = pd.to_datetime(selected_orders["order_purchase_timestamp"], errors="coerce")
    selected_orders = selected_orders.sort_values(["_ts", "order_id"]).drop(columns=["_ts"])

    selected_customers = customers[customers["customer_id"].isin(selected_orders["customer_id"])].copy()
    selected_items = items[items["order_id"].isin(selected)].copy()
    selected_payments = payments[payments["order_id"].isin(selected)].copy()
    selected_products = products[products["product_id"].isin(selected_items["product_id"])].copy()

    categories = set(selected_products["product_category_name"].dropna().astype(str))
    selected_translation = tables["translation"][
        tables["translation"]["product_category_name"].astype(str).isin(categories)
    ].copy()

    dest.mkdir(parents=True, exist_ok=True)
    frames = {
        "orders": selected_orders,
        "customers": selected_customers,
        "items": selected_items,
        "payments": selected_payments,
        "products": selected_products,
        "translation": selected_translation,
    }
    for key, frame in frames.items():
        frame.to_csv(dest / FILES[key], index=False, lineterminator="\n")
    return {key: int(len(frame)) for key, frame in frames.items()}


def _facts(dest: Path) -> dict[str, Any]:
    dataset = OlistDataset(dest)
    repeat = reference_repeat_purchase_rate(dataset)
    category = reference_category_sales(dataset)
    payments = reference_payment_diagnostics(dataset)
    orders = dataset.table("orders")
    return {
        "row_counts": {
            "orders": int(len(orders)),
            "customers": int(len(dataset.table("customers"))),
            "order_items": int(len(dataset.table("order_items"))),
            "order_payments": int(len(dataset.table("order_payments"))),
            "products": int(len(dataset.table("products"))),
        },
        "status_counts": {
            status: int(count)
            for status, count in orders["order_status"].value_counts().sort_index().items()
        },
        "repeat_purchase_reference": {
            "repeat_customers": repeat["repeat_customers"],
            "persistent_customers": repeat["persistent_customers"],
            "formatted": repeat["formatted"],
            "wrong_plan_order_record_repeat_groups": repeat["order_record_key_repeat_groups"],
        },
        "category_sales_reference": {
            "total_sales": category["total_sales"],
            "formatted": category["formatted"],
            "output_rows": category["output_rows"],
            "item_rows": category["item_rows"],
        },
        "payment_grain_diagnostics": {
            "distinct_delivered_payment_total": payments["distinct_delivered_payment_total"],
            "item_grain_payment_total": payments["item_grain_payment_total"],
            "formatted_distinct": payments["formatted_distinct"],
            "formatted_inflated": payments["formatted_inflated"],
        },
    }


def _check(facts: dict[str, Any]) -> None:
    repeat = facts["repeat_purchase_reference"]
    category = facts["category_sales_reference"]
    payments = facts["payment_grain_diagnostics"]
    problems: list[str] = []
    if repeat["repeat_customers"] < 1:
        problems.append("the sample has no repeat persistent customer")
    if repeat["wrong_plan_order_record_repeat_groups"] != 0:
        problems.append("the wrong customer_id plan would not report 0%")
    if category["total_sales"] <= 0 or category["item_rows"] < 1:
        problems.append("the sample has no category-sales items")
    if abs(payments["item_grain_payment_total"] - payments["distinct_delivered_payment_total"]) < 1e-6:
        problems.append("payment values do not inflate at item grain")
    if problems:
        raise SystemExit("Mini dataset does not preserve the demo failure shapes: " + "; ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data" / "olist", help="Full Olist CSV directory.")
    parser.add_argument("--dest", type=Path, default=ROOT / "data" / "olist_mini", help="Destination directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing mini dataset.")
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    dest = args.dest.expanduser().resolve()
    if not args.force and any((dest / name).exists() for name in FILES.values()):
        raise SystemExit(f"{dest} already contains a mini dataset. Re-run with --force to overwrite.")

    tables = _read(source)
    selected, identities = _select(tables)
    row_counts = _write(tables, selected, dest)
    facts = _facts(dest)
    _check(facts)

    try:
        source_label = str(source.relative_to(ROOT))
    except ValueError:
        source_label = str(source)
    manifest = {
        "kind": "olist_mini",
        "source": source_label,
        "selection": {
            "orders": TOTAL_ORDERS,
            "delivered_repeat_orders": list(DELIVERED_REPEAT_ORDERS),
            "single_customer_orders": SINGLE_ORDERS,
            "canceled_orders": CANCELED_ORDERS,
            **identities,
        },
        "row_counts": row_counts,
        **facts,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {dest}")
    for key, count in row_counts.items():
        print(f"  {key:12s} {count:>3d} rows")
    print(
        "  repeat reference "
        f"{facts['repeat_purchase_reference']['formatted']} "
        f"({facts['repeat_purchase_reference']['repeat_customers']}/"
        f"{facts['repeat_purchase_reference']['persistent_customers']} persistent customers); "
        f"wrong customer_id plan groups with repeats = "
        f"{facts['repeat_purchase_reference']['wrong_plan_order_record_repeat_groups']}"
    )
    print(
        "  category reference "
        f"{facts['category_sales_reference']['formatted']} vs inflated payment total "
        f"{facts['payment_grain_diagnostics']['formatted_inflated']}"
    )
    print("\nUse it in a separate process so the full-data server keeps its dataset:")
    print(f"  OLIST_DATA_DIR={dest} RUNS_DIR={ROOT / 'runs_mini'} PORT=8001 ./scripts/start.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
