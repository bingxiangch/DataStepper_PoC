"""Olist dataset loading, schema metadata, and safe row filters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .config import DATA_DIR

TABLE_FILES = {
    "orders": "olist_orders_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "products": "olist_products_dataset.csv",
}

DATETIME_COLUMNS = {
    "orders": ["order_purchase_timestamp", "order_approved_at", "order_delivered_customer_date"],
    "order_items": ["shipping_limit_date"],
}

DATA_DICTIONARY: dict[str, dict[str, str]] = {
    "orders": {
        "order_id": "Unique order identifier; one row per order.",
        "customer_id": "Order-associated customer-record key. In Olist this key is unique per order, so it does not by itself identify a persistent customer across orders.",
        "order_status": "Order lifecycle status (delivered, shipped, canceled, ...).",
        "order_purchase_timestamp": "Purchase timestamp used for time scoping and monthly grouping.",
    },
    "customers": {
        "customer_id": "Customer record key; joins one-to-one to orders.customer_id. Record identity is order-specific.",
        "customer_unique_id": "Persistent-customer identifier linking customer records (and therefore orders) that belong to the same customer across purchases.",
        "customer_zip_code_prefix": "Customer ZIP-code prefix.",
        "customer_city": "Customer city.",
        "customer_state": "Customer state.",
    },
    "order_items": {
        "order_id": "Order identifier; multiple item rows can share an order.",
        "order_item_id": "Line number within an order; (order_id, order_item_id) identifies an item row.",
        "product_id": "Product identifier; joins to products.product_id.",
        "seller_id": "Seller identifier.",
        "price": "Item price in BRL, excluding freight_value.",
        "freight_value": "Freight charge in BRL. Not part of item sales defined as SUM(price).",
    },
    "order_payments": {
        "order_id": "Order identifier; multiple payment records can share an order.",
        "payment_sequential": "Sequence number distinguishing payment records within an order. Payment-record identity is (order_id, payment_sequential).",
        "payment_type": "Payment method.",
        "payment_installments": "Number of installments.",
        "payment_value": "Payment amount in BRL at payment-record grain; not an item-level measure.",
    },
    "products": {
        "product_id": "Product identifier; one row per product.",
        "product_category_name": "Product category label (Portuguese). Null means the category is missing.",
    },
}

def _table_note(table: str, df: pd.DataFrame) -> str:
    """Describe the loaded table using measured counts.

    Notes are derived from the frame rather than the published full-Olist
    counts, so a small test dataset never reports misleading row totals.
    """
    rows = len(df)
    if table == "orders":
        distinct = int(df["customer_id"].nunique(dropna=True)) if "customer_id" in df else 0
        qualifier = "one distinct value per order" if distinct == rows else f"{distinct:,} distinct values"
        return f"{rows:,} orders; customer_id has {qualifier}."
    if table == "customers":
        distinct = (
            int(df["customer_unique_id"].nunique(dropna=True))
            if "customer_unique_id" in df
            else 0
        )
        return f"{rows:,} customer records; {distinct:,} persistent customer_unique_id values."
    if table == "order_items":
        return f"{rows:,} item rows; price excludes freight."
    if table == "order_payments":
        return f"{rows:,} payment records; use (order_id, payment_sequential) as record identity."
    if table == "products":
        return f"{rows:,} products with category labels."
    return ""


class DatasetError(RuntimeError):
    pass


class OlistDataset:
    def __init__(self, data_dir: Path | str = DATA_DIR):
        self.data_dir = Path(data_dir)
        self._tables: dict[str, pd.DataFrame] | None = None

    @property
    def available(self) -> bool:
        return all((self.data_dir / name).exists() for name in TABLE_FILES.values())

    def _load(self) -> dict[str, pd.DataFrame]:
        if self._tables is not None:
            return self._tables
        missing = [str(self.data_dir / f) for f in TABLE_FILES.values() if not (self.data_dir / f).exists()]
        if missing:
            raise DatasetError(
                "Olist CSV files are missing: "
                + ", ".join(missing)
                + ". Run scripts/prepare_data.py or set OLIST_DATA_DIR."
            )
        tables: dict[str, pd.DataFrame] = {}
        for table, filename in TABLE_FILES.items():
            df = pd.read_csv(self.data_dir / filename)
            for col in DATETIME_COLUMNS.get(table, []):
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
            tables[table] = df
        self._tables = tables
        return tables

    def table(self, name: str) -> pd.DataFrame:
        if name not in TABLE_FILES:
            raise DatasetError(f"Unknown table {name!r}. Available: {sorted(TABLE_FILES)}")
        return self._load()[name]

    def tables(self) -> dict[str, pd.DataFrame]:
        return self._load()

    def source_artifact(self, table: str) -> str:
        return f"src:{table}"

    def schema(self, table: str, columns: list[str] | None = None) -> dict[str, Any]:
        df = self.table(table)
        selected = columns or list(df.columns)
        missing = [c for c in selected if c not in df.columns]
        if missing:
            raise DatasetError(f"Columns {missing} do not exist in {table!r}.")
        out = []
        for col in selected:
            series = df[col]
            item: dict[str, Any] = {
                "name": col,
                "dtype": str(series.dtype),
                "nulls": int(series.isna().sum()),
                "unique": int(series.nunique(dropna=True)),
                "semantics": DATA_DICTIONARY.get(table, {}).get(col, ""),
            }
            if pd.api.types.is_numeric_dtype(series):
                clean = series.dropna()
                if len(clean):
                    item["min"] = float(clean.min())
                    item["max"] = float(clean.max())
            out.append(item)
        return {
            "table": table,
            "rows": int(len(df)),
            "columns": out,
            "note": _table_note(table, df),
        }

    def all_schema(self) -> dict[str, Any]:
        return {name: self.schema(name) for name in TABLE_FILES}

    def profile(self, table: str, column: str, top_n: int = 5) -> dict[str, Any]:
        df = self.table(table)
        if column not in df.columns:
            raise DatasetError(f"Column {column!r} does not exist in {table!r}.")
        series = df[column]
        out: dict[str, Any] = {
            "table": table,
            "column": column,
            "dtype": str(series.dtype),
            "rows": int(len(series)),
            "nulls": int(series.isna().sum()),
            "unique": int(series.nunique(dropna=True)),
            "semantics": DATA_DICTIONARY.get(table, {}).get(column, ""),
        }
        if pd.api.types.is_numeric_dtype(series):
            clean = series.dropna()
            if len(clean):
                out.update(
                    {
                        "min": float(clean.min()),
                        "max": float(clean.max()),
                        "mean": float(clean.mean()),
                    }
                )
        if pd.api.types.is_datetime64_any_dtype(series):
            clean = series.dropna()
            if len(clean):
                out["min"] = str(clean.min())
                out["max"] = str(clean.max())
        counts = series.fillna("<null>").value_counts().head(top_n)
        out["top_values"] = [{"value": str(k), "count": int(v)} for k, v in counts.items()]
        return out
