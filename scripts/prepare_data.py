#!/usr/bin/env python3
"""Validate (and optionally stage) the local Olist CSV files.

This script never downloads data and never executes CSV content.  It copies
only the six expected filenames from a directory you provide, then validates
their headers and counts rows.

Usage:
    python scripts/prepare_data.py
    python scripts/prepare_data.py --source ~/Downloads/olist_csv
    python scripts/prepare_data.py --source ~/Downloads/olist_csv --force
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / "data" / "olist"

EXPECTED: dict[str, list[str]] = {
    "olist_orders_dataset.csv": [
        "order_id",
        "customer_id",
        "order_status",
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ],
    "olist_customers_dataset.csv": [
        "customer_id",
        "customer_unique_id",
        "customer_zip_code_prefix",
        "customer_city",
        "customer_state",
    ],
    "olist_order_items_dataset.csv": [
        "order_id",
        "order_item_id",
        "product_id",
        "seller_id",
        "shipping_limit_date",
        "price",
        "freight_value",
    ],
    "olist_order_payments_dataset.csv": [
        "order_id",
        "payment_sequential",
        "payment_type",
        "payment_installments",
        "payment_value",
    ],
    "olist_products_dataset.csv": [
        "product_id",
        "product_category_name",
        "product_name_lenght",
        "product_description_lenght",
        "product_photos_qty",
        "product_weight_g",
        "product_length_cm",
        "product_height_cm",
        "product_width_cm",
    ],
    "product_category_name_translation.csv": [
        "product_category_name",
        "product_category_name_english",
    ],
}


def validate(path: Path) -> int:
    # utf-8-sig accepts both plain UTF-8 and the BOM that Kaggle ships on the
    # translation CSV, so the first header name is never prefixed by ﻿.
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"{path.name} is empty") from exc
        missing = [column for column in EXPECTED[path.name] if column not in header]
        if missing:
            raise ValueError(f"{path.name} is missing columns: {', '.join(missing)}")
        rows = sum(1 for _ in reader)
    return rows


def stage(source: Path, dest: Path, force: bool) -> None:
    missing = [name for name in EXPECTED if not (source / name).is_file()]
    if missing:
        raise SystemExit(
            "Source is missing these files:\n  " + "\n  ".join(missing) + f"\nSource looked in: {source}"
        )
    dest.mkdir(parents=True, exist_ok=True)
    for name in EXPECTED:
        target = dest / name
        if target.exists() and not force:
            print(f"keep     {name}")
            continue
        shutil.copy2(source / name, target)
        print(f"copied   {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        help="Directory containing the six Olist CSV files. Omit to validate the default directory.",
    )
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="Destination directory (default: data/olist).")
    parser.add_argument("--force", action="store_true", help="Overwrite existing destination files.")
    args = parser.parse_args()

    dest = args.dest.resolve()
    if args.source:
        stage(args.source.expanduser().resolve(), dest, args.force)

    available = [name for name in EXPECTED if (dest / name).is_file()]
    if len(available) != len(EXPECTED):
        missing = sorted(set(EXPECTED) - set(available))
        print(
            "Olist data is incomplete.\n"
            f"Expected directory: {dest}\n"
            "Missing:\n  " + "\n  ".join(missing) + "\n\n"
            "Download the Brazilian E-Commerce Public Dataset by Olist from Kaggle, then run:\n"
            "  python scripts/prepare_data.py --source /path/to/extracted/csvs\n",
            file=sys.stderr,
        )
        return 1

    total_bytes = 0
    print(f"\nValidating {dest}")
    for name in EXPECTED:
        path = dest / name
        try:
            rows = validate(path)
        except (OSError, ValueError, csv.Error) as exc:
            print(f"INVALID  {name}: {exc}", file=sys.stderr)
            return 1
        total_bytes += path.stat().st_size
        print(f"ok       {name:45s} {rows:>8,} rows")
    print(f"\nReady: {len(EXPECTED)} files, {total_bytes / (1024 * 1024):.1f} MiB")
    print("Run scripts/data_facts.py to print the independent reference values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
