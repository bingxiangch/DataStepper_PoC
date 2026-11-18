"""Validated data tools and deterministic evidence collection.

The agent can only ask for these operations.  Tools never execute arbitrary
Python, shell commands, or SQL supplied by the model.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from .models import Fact, MetricDefinition, MetricValue, Report, utcnow


class ToolError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class EvidenceDraft:
    kind: str
    label: str
    summary: str
    facts: list[Fact] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    updates: dict[str, pd.DataFrame] = field(default_factory=dict)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    evidence: list[EvidenceDraft] = field(default_factory=list)
    metric: MetricValue | None = None
    report: Report | None = None
    requires_approval: bool = False
    preview: dict[str, Any] | None = None
    summary: str = ""


def fmt_int(value: Any) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}"


def fmt_num(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-12 and abs(number) < 1e15:
        return f"{int(round(number)):,}"
    return f"{number:,.{digits}f}"


def fmt_pct(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.{digits}f}%"


def fmt_currency(value: Any) -> str:
    if value is None:
        return "—"
    return f"R$ {float(value):,.2f}"


def format_metric(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "percent":
        return f"{value * 100:.4f}%"
    if unit == "currency":
        return f"R$ {value:,.2f}"
    return fmt_num(value)


def _as_list(value: Any, name: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return value
    raise ToolError("invalid_args", f"{name} must be a non-empty string or list of strings")


def _table(tables: dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
    if not isinstance(name, str) or name not in tables:
        raise ToolError("unknown_table", f"Table {name!r} does not exist. Available: {sorted(tables)}")
    return tables[name]


def _columns(df: pd.DataFrame, columns: list[str], table: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ToolError(
            "unknown_column",
            f"Column(s) {missing} do not exist in {table!r}. Available: {list(df.columns)}",
        )


def _new_output(tables: dict[str, pd.DataFrame], name: Any) -> str:
    if not isinstance(name, str) or not name:
        raise ToolError("invalid_args", "output must be a non-empty table name")
    if name in tables:
        raise ToolError(
            "output_exists",
            f"output table {name!r} already exists; choose a new table name so history stays immutable",
        )
    return name


_FILTER_OPS = {
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "is_null", "not_null", "contains"
}


def _coerce_value(series: pd.Series, value: Any) -> Any:
    if pd.api.types.is_datetime64_any_dtype(series) and isinstance(value, str):
        return pd.Timestamp(value)
    if pd.api.types.is_numeric_dtype(series) and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def condition_mask(df: pd.DataFrame, where: Any) -> pd.Series:
    if where is None:
        return pd.Series(True, index=df.index)
    if isinstance(where, dict):
        where = [where]
    if not isinstance(where, list) or not where:
        raise ToolError("invalid_condition", "where must be a non-empty list of conditions")
    mask = pd.Series(True, index=df.index)
    for item in where:
        if not isinstance(item, dict):
            raise ToolError("invalid_condition", "each condition must be an object")
        column = item.get("column")
        op = item.get("op", "eq")
        if column not in df.columns:
            raise ToolError("unknown_column", f"Condition column {column!r} is not available")
        if op not in _FILTER_OPS:
            raise ToolError("invalid_condition", f"Unsupported filter op {op!r}")
        series = df[column]
        if op == "is_null":
            current = series.isna()
        elif op == "not_null":
            current = series.notna()
        else:
            if "value" not in item:
                raise ToolError("invalid_condition", f"Condition {column!r} with op {op!r} needs a value")
            raw = item["value"]
            if op in {"in", "not_in"}:
                if not isinstance(raw, list):
                    raise ToolError("invalid_condition", f"Condition {column!r} with {op!r} needs a list")
                values = [_coerce_value(series, v) for v in raw]
                current = series.isin(values)
                if op == "not_in":
                    current = ~current
            elif op == "contains":
                current = series.astype("string").str.contains(str(raw), case=False, na=False)
            else:
                value = _coerce_value(series, raw)
                comparisons = {
                    "eq": lambda s: s == value,
                    "ne": lambda s: s != value,
                    "gt": lambda s: s > value,
                    "gte": lambda s: s >= value,
                    "lt": lambda s: s < value,
                    "lte": lambda s: s <= value,
                }
                try:
                    current = comparisons[op](series)
                except TypeError as exc:
                    raise ToolError("invalid_condition", f"Cannot compare {column!r} with {raw!r}: {exc}") from exc
        mask = mask & current.fillna(False)
    return mask


def describe_where(where: Any) -> str:
    if not where:
        return "all rows"
    items = where if isinstance(where, list) else [where]
    parts = []
    for item in items:
        col = item.get("column")
        op = item.get("op", "eq")
        val = item.get("value", "")
        parts.append(f"{col} {op} {json.dumps(val, default=str)}")
    return " AND ".join(parts)


def _schema_facts(schema: dict[str, Any]) -> list[Fact]:
    facts = [Fact(label="Rows", value=fmt_int(schema["rows"])), Fact(label="Columns inspected", value=str(len(schema["columns"])))]
    for col in schema["columns"][:3]:
        facts.append(Fact(label=f"{col['name']} unique", value=fmt_int(col["unique"])))
    return facts


def _inspect_schema(args: dict[str, Any], dataset) -> ToolResult:
    table = args.get("table")
    if table is None:
        raise ToolError("invalid_args", "inspect_schema requires table")
    columns = args.get("columns")
    if columns is not None and not isinstance(columns, list):
        raise ToolError("invalid_args", "columns must be a list")
    schema = dataset.schema(table, columns)
    sem = [
        {"column": c["name"], "semantics": c["semantics"], "unique": c["unique"], "nulls": c["nulls"]}
        for c in schema["columns"]
    ]
    summary = (
        f"{table}: {fmt_int(schema['rows'])} rows, {len(schema['columns'])} column(s) inspected. "
        + "; ".join(f"{c['name']} has {fmt_int(c['unique'])} distinct values" for c in schema["columns"][:2])
    )
    return ToolResult(
        inputs=[table],
        evidence=[
            EvidenceDraft(
                kind="schema",
                label=f"SCHEMA · {table}",
                summary=summary,
                facts=_schema_facts(schema),
                details={"columns": schema["columns"], "semantics": sem, "note": schema.get("note", "")},
            )
        ],
        summary=f"Inspect schema for {table}",
    )


def _inspect_dictionary(args: dict[str, Any], dataset) -> ToolResult:
    from .dataset import DATA_DICTIONARY

    table = args.get("table")
    if table is not None and table not in DATA_DICTIONARY:
        raise ToolError("unknown_table", f"Unknown table {table!r}")
    selected = {table: DATA_DICTIONARY[table]} if table else DATA_DICTIONARY
    facts = [Fact(label="Tables documented", value=str(len(selected)))]
    for name, cols in selected.items():
        facts.append(Fact(label=name, value=f"{len(cols)} documented columns"))
    return ToolResult(
        inputs=[table] if table else [],
        evidence=[
            EvidenceDraft(
                kind="data_dictionary",
                label="DATA DICTIONARY",
                summary="Documented column semantics used to validate business bindings.",
                facts=facts[:4],
                details={"dictionary": selected},
            )
        ],
        summary="Inspect data dictionary",
    )


def _profile_column(args: dict[str, Any], dataset) -> ToolResult:
    table = args.get("table")
    column = args.get("column")
    if not table or not column:
        raise ToolError("invalid_args", "profile_column requires table and column")
    top_n = int(args.get("top_n", 5))
    top_n = max(1, min(top_n, 10))
    profile = dataset.profile(table, column, top_n)
    facts = [
        Fact(label="Rows", value=fmt_int(profile["rows"])),
        Fact(label="Distinct", value=fmt_int(profile["unique"])),
        Fact(label="Nulls", value=fmt_int(profile["nulls"])),
    ]
    if profile.get("top_values"):
        top = profile["top_values"][0]
        facts.append(Fact(label="Most frequent", value=f"{top['value']} ({fmt_int(top['count'])})"))
    return ToolResult(
        inputs=[table],
        evidence=[
            EvidenceDraft(
                kind="profile",
                label=f"PROFILE · {table}.{column}",
                summary=(
                    f"{table}.{column}: {fmt_int(profile['rows'])} rows, "
                    f"{fmt_int(profile['unique'])} distinct, {fmt_int(profile['nulls'])} null."
                ),
                facts=facts,
                details=profile,
            )
        ],
        summary=f"Profile {table}.{column}",
    )


def _sample_records(args: dict[str, Any], tables: dict[str, pd.DataFrame]) -> ToolResult:
    table = args.get("table")
    if not table:
        raise ToolError("invalid_args", "sample_records requires table")
    df = _table(tables, table)
    n = max(1, min(int(args.get("n", 5)), 10))
    columns = args.get("columns")
    if columns is not None:
        columns = _as_list(columns, "columns")
        _columns(df, columns, table)
        sample = df[columns].head(n)
    else:
        sample = df.head(n)
    sanitized = json.loads(sample.to_json(orient="records", date_format="iso"))
    return ToolResult(
        inputs=[table],
        evidence=[
            EvidenceDraft(
                kind="sample",
                label=f"SAMPLE · {table}",
                summary=f"First {len(sample)} row(s) from {table} ({len(df.columns)} columns).",
                facts=[Fact(label="Returned rows", value=str(len(sample)))],
                details={"columns": list(sample.columns), "records": sanitized},
            )
        ],
        summary=f"Sample {table}",
    )


def _filter_rows(args: dict[str, Any], tables: dict[str, pd.DataFrame]) -> ToolResult:
    table = args.get("table")
    output = _new_output(tables, args.get("output"))
    df = _table(tables, table)
    where = args.get("where")
    mask = condition_mask(df, where)
    result = df[mask].copy()
    removed = len(df) - len(result)
    details: dict[str, Any] = {
        "condition": describe_where(where),
        "input_rows": int(len(df)),
        "output_rows": int(len(result)),
        "removed_rows": int(removed),
    }
    for col in ("order_status",):
        if col in df.columns and col not in result.columns:
            continue
        if col in df.columns:
            counts = df[col].fillna("<null>").value_counts().head(8)
            details[f"{col}_distribution_before"] = {str(k): int(v) for k, v in counts.items()}
    return ToolResult(
        updates={output: result},
        inputs=[table],
        outputs=[output],
        evidence=[
            EvidenceDraft(
                kind="filter",
                label=f"FILTER · {table} → {output}",
                summary=(
                    f"Kept {fmt_int(len(result))} of {fmt_int(len(df))} rows "
                    f"({fmt_int(removed)} removed) where {describe_where(where)}."
                ),
                facts=[
                    Fact(label="Rows in", value=fmt_int(len(df))),
                    Fact(label="Rows out", value=fmt_int(len(result))),
                    Fact(label="Removed", value=fmt_int(removed)),
                ],
                details=details,
            )
        ],
        summary=f"Filter {table} → {output}",
    )


def _join_tables(args: dict[str, Any], tables: dict[str, pd.DataFrame]) -> ToolResult:
    left_name = args.get("left")
    right_name = args.get("right")
    output = _new_output(tables, args.get("output"))
    left = _table(tables, left_name)
    right = _table(tables, right_name)
    left_on = _as_list(args.get("left_on"), "left_on")
    right_on = _as_list(args.get("right_on", left_on), "right_on")
    if len(left_on) != len(right_on):
        raise ToolError("invalid_join", "left_on and right_on must have the same length")
    _columns(left, left_on, left_name)
    _columns(right, right_on, right_name)
    how = args.get("how", "inner")
    if how not in {"inner", "left", "right", "outer"}:
        raise ToolError("invalid_join", f"Unsupported join type {how!r}")

    try:
        merged = pd.merge(
            left,
            right,
            left_on=left_on,
            right_on=right_on,
            how=how,
            suffixes=("", "_right"),
        )
    except (pd.errors.MergeError, KeyError, ValueError) as exc:
        raise ToolError("invalid_join", str(exc)) from exc

    right_sizes = right.groupby(right_on, dropna=False).size() if right_on else pd.Series(dtype=int)
    max_right_per_key = int(right_sizes.max()) if len(right_sizes) else 0
    duplicate_right_keys = int((right_sizes > 1).sum()) if len(right_sizes) else 0
    facts = [
        Fact(label="Left rows", value=fmt_int(len(left))),
        Fact(label="Right rows", value=fmt_int(len(right))),
        Fact(label="Output rows", value=fmt_int(len(merged))),
        Fact(label="Right keys repeating", value=fmt_int(duplicate_right_keys)),
    ]
    details: dict[str, Any] = {
        "left": left_name,
        "right": right_name,
        "left_on": left_on,
        "right_on": right_on,
        "how": how,
        "left_rows": int(len(left)),
        "right_rows": int(len(right)),
        "output_rows": int(len(merged)),
        "distinct_right_keys": int(len(right_sizes)),
        "right_keys_with_multiple_records": duplicate_right_keys,
        "max_right_records_per_key": max_right_per_key,
    }

    # Payment contributions are order-level records.  When they meet item rows,
    # each payment record can be repeated.  Measure that effect from actual rows.
    identity_cols = [c for c in ("order_id", "payment_sequential") if c in right.columns]
    if (
        len(identity_cols) == 2
        and "payment_value" in right.columns
        and "payment_sequential" in merged.columns
        and "payment_value" in merged.columns
    ):
        copies = merged.groupby(identity_cols, dropna=False).size()
        distinct_right = right.drop_duplicates(identity_cols)
        joined_payment_rows = int(len(merged))
        distinct_payment_records = int(len(copies))
        repeated_records = int((copies > 1).sum())
        max_copies = int(copies.max()) if len(copies) else 0
        avg_copies = float(joined_payment_rows / distinct_payment_records) if distinct_payment_records else 0
        joined_value = float(pd.to_numeric(merged["payment_value"], errors="coerce").fillna(0).sum())
        distinct_value = float(pd.to_numeric(distinct_right["payment_value"], errors="coerce").fillna(0).sum())
        inflation = joined_value / distinct_value if distinct_value else None
        details.update(
            {
                "payment_records_after_join": distinct_payment_records,
                "joined_rows_carrying_payment": joined_payment_rows,
                "payment_records_repeated": repeated_records,
                "average_copies_per_payment_record": round(avg_copies, 4),
                "max_copies_per_payment_record": max_copies,
                "payment_value_joined_sum": round(joined_value, 2),
                "payment_value_distinct_sum": round(distinct_value, 2),
                "payment_value_inflation_ratio": round(inflation, 6) if inflation else None,
            }
        )
        facts.extend(
            [
                Fact(label="Payment records", value=fmt_int(distinct_payment_records)),
                Fact(label="Repeated payment records", value=fmt_int(repeated_records)),
                Fact(label="Max copies / payment", value=fmt_int(max_copies)),
                Fact(
                    label="SUM(payment_value)",
                    value=f"{fmt_currency(joined_value)} joined vs {fmt_currency(distinct_value)} distinct",
                ),
            ]
        )

    summary = (
        f"Joined {left_name} ({fmt_int(len(left))}) with {right_name} ({fmt_int(len(right))}) "
        f"on {', '.join(left_on)} → {fmt_int(len(merged))} rows. "
        f"Right keys repeating: {fmt_int(duplicate_right_keys)}; max records per key: {fmt_int(max_right_per_key)}."
    )
    return ToolResult(
        updates={output: merged},
        inputs=[left_name, right_name],
        outputs=[output],
        evidence=[
            EvidenceDraft(
                kind="join",
                label=f"JOIN · {left_name} × {right_name}",
                summary=summary,
                facts=facts[:7],
                details=details,
            )
        ],
        summary=f"Join {left_name} × {right_name} → {output}",
    )


def _group_and_aggregate(args: dict[str, Any], tables: dict[str, pd.DataFrame]) -> ToolResult:
    table = args.get("table")
    output = _new_output(tables, args.get("output"))
    df = _table(tables, table)
    by = _as_list(args.get("by"), "by")
    _columns(df, by, table)
    aggregations = args.get("aggregations")
    if not isinstance(aggregations, list) or not aggregations:
        raise ToolError("invalid_args", "aggregations must be a non-empty list")
    date_part = args.get("date_part")
    working = df
    group_keys = list(by)
    if date_part:
        if not isinstance(date_part, dict) or not date_part.get("column"):
            raise ToolError("invalid_args", "date_part needs column and alias")
        dc = date_part["column"]
        alias = date_part.get("alias", "month")
        unit = date_part.get("unit", "month")
        if dc not in df.columns:
            raise ToolError("unknown_column", f"date_part column {dc!r} is missing")
        if unit not in {"year", "quarter", "month", "day"}:
            raise ToolError("invalid_args", f"Unsupported date unit {unit!r}")
        working = df.copy()
        values = pd.to_datetime(working[dc], errors="coerce")
        if unit == "month":
            working[alias] = values.dt.to_period("M").astype("string")
        elif unit == "quarter":
            working[alias] = values.dt.to_period("Q").astype("string")
        elif unit == "year":
            working[alias] = values.dt.year.astype("Int64").astype("string")
        else:
            working[alias] = values.dt.date.astype("string")
        group_keys = [alias] + group_keys

    named: dict[str, tuple[str, str]] = {}
    agg_meta: list[dict[str, Any]] = []
    for spec in aggregations:
        if not isinstance(spec, dict):
            raise ToolError("invalid_args", "each aggregation must be an object")
        func = spec.get("func")
        if func not in {"sum", "count", "mean", "min", "max", "nunique"}:
            raise ToolError("invalid_args", f"Unsupported aggregation func {func!r}")
        column = spec.get("column")
        alias = spec.get("alias") or f"{func}_{column or 'rows'}"
        if func == "count" and column in (None, "*"):
            continue
        if not column:
            raise ToolError("invalid_args", f"Aggregation {func!r} needs a column (or '*' for count)")
        _columns(working, [column], table)
        if alias in working.columns or alias in group_keys or alias in named:
            raise ToolError("invalid_args", f"Aggregation alias {alias!r} conflicts with an existing column")
        named[alias] = (column, func)
        agg_meta.append({"column": column, "func": func, "alias": alias})

    grouped = working.groupby(group_keys, dropna=False, observed=True)
    pieces = []
    size_counts = grouped.size()
    if not named:
        result = size_counts.reset_index(name=aggregations[0].get("alias") or "row_count")
        agg_meta.append({"column": "*", "func": "count", "alias": aggregations[0].get("alias") or "row_count"})
    else:
        frame = grouped.agg(**named).reset_index()
        result = frame
        # `count` with "*" is not expressible in named aggregation; add it separately.
        for spec in aggregations:
            if spec.get("func") == "count" and spec.get("column") in (None, "*"):
                alias = spec.get("alias") or "row_count"
                if alias in result.columns:
                    raise ToolError("invalid_args", f"Aggregation alias {alias!r} is duplicated")
                size_frame = grouped.size().reset_index(name=alias)
                result = result.merge(size_frame, on=group_keys, how="left")
                agg_meta.append({"column": "*", "func": "count", "alias": alias})

    # Group-level row counts are evidence of the grain, even when aggregating sums.
    sizes = size_counts
    groups = int(len(sizes))
    max_group = int(sizes.max()) if groups else 0
    groups_multi = int((sizes > 1).sum()) if groups else 0
    singleton = int((sizes == 1).sum()) if groups else 0
    size_buckets = {
        "1": singleton,
        "2": int((sizes == 2).sum()) if groups else 0,
        "3-5": int(((sizes >= 3) & (sizes <= 5)).sum()) if groups else 0,
        "6-10": int(((sizes >= 6) & (sizes <= 10)).sum()) if groups else 0,
        "11+": int((sizes > 10).sum()) if groups else 0,
    }
    facts = [
        Fact(label="Rows in → groups", value=f"{fmt_int(len(df))} → {fmt_int(groups)}"),
        Fact(label="Largest group", value=f"{fmt_int(max_group)} rows"),
        Fact(label="Groups with ≥2 rows", value=fmt_int(groups_multi)),
    ]
    agg_details = []
    for meta in agg_meta:
        alias = meta["alias"]
        if alias not in result.columns:
            continue
        series = pd.to_numeric(result[alias], errors="coerce")
        item = {"alias": alias, "func": meta["func"], "column": meta["column"]}
        if series.notna().any():
            item.update(
                {
                    "max": float(series.max()),
                    "total": float(series.sum()),
                    "groups_at_least_2": int((series >= 2).sum()),
                }
            )
            if meta["func"] == "count":
                facts.append(Fact(label=f"Max {alias}", value=fmt_num(series.max())))
                facts.append(Fact(label=f"Groups with {alias} ≥2", value=fmt_int((series >= 2).sum())))
                facts.append(Fact(label=f"Total {alias}", value=fmt_num(series.sum())))
            elif meta["func"] == "sum":
                facts.append(Fact(label=f"Total {alias}", value=fmt_currency(series.sum())))
        agg_details.append(item)
    details = {
        "group_keys": group_keys,
        "input_rows": int(len(df)),
        "output_groups": groups,
        "largest_group_rows": max_group,
        "groups_with_multiple_rows": groups_multi,
        "singleton_groups": singleton,
        "group_size_distribution": size_buckets,
        "aggregations": agg_details,
        "date_part": date_part,
    }
    summary = (
        f"Grouped {fmt_int(len(df))} rows by {', '.join(group_keys)} into {fmt_int(groups)} groups; "
        f"largest group {fmt_int(max_group)} row(s), {fmt_int(groups_multi)} group(s) contain ≥2 rows."
    )
    return ToolResult(
        updates={output: result},
        inputs=[table],
        outputs=[output],
        evidence=[
            EvidenceDraft(
                kind="groupby",
                label=f"GROUP · {', '.join(group_keys)} → {output}",
                summary=summary,
                facts=facts[:7],
                details=details,
            )
        ],
        summary=f"Group {table} by {', '.join(group_keys)} → {output}",
    )


def _compute_metric(args: dict[str, Any], tables: dict[str, pd.DataFrame]) -> ToolResult:
    table = args.get("table")
    df = _table(tables, table)
    name = args.get("name")
    if not name:
        raise ToolError("invalid_args", "compute_metric requires name")
    kind = args.get("kind", "share_of_rows")
    unit = args.get("unit", "percent" if kind == "share_of_rows" else "number")
    definition = args.get("definition", "")
    output = args.get("output") or f"metric_{name}"
    output = _new_output(tables, output)

    numerator: float | None = None
    denominator: float | None = None
    if kind == "share_of_rows":
        mask = condition_mask(df, args.get("where"))
        numerator = float(mask.sum())
        denominator = float(len(df))
        value = numerator / denominator if denominator else None
    elif kind in {"sum", "mean"}:
        column = args.get("column")
        if not column:
            raise ToolError("invalid_args", f"{kind} requires column")
        _columns(df, [column], table)
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        value = float(series.sum() if kind == "sum" else series.mean()) if len(series) else None
        denominator = float(len(series))
    else:
        raise ToolError("invalid_args", f"Unsupported metric kind {kind!r}")

    formatted = format_metric(value, unit)
    result = pd.DataFrame(
        [
            {
                "metric": name,
                "value": value,
                "numerator": numerator,
                "denominator": denominator,
                "unit": unit,
                "definition": definition,
            }
        ]
    )
    metric = MetricValue(
        name=name,
        value=value,
        formatted=formatted,
        numerator=numerator,
        denominator=denominator,
        unit=unit,
        definition=definition,
        extras={"table": table, "kind": kind, "rows": int(len(df))},
    )
    if kind == "share_of_rows":
        summary = (
            f"{name}: {fmt_int(numerator)} / {fmt_int(denominator)} = {formatted}. "
            f"Condition: {describe_where(args.get('where'))}."
        )
        facts = [
            Fact(label=args.get("numerator_label", "Numerator"), value=fmt_int(numerator)),
            Fact(label=args.get("denominator_label", "Denominator"), value=fmt_int(denominator)),
            Fact(label="Metric value", value=formatted),
        ]
    else:
        summary = f"{name}: {kind} over {table} = {formatted}."
        facts = [
            Fact(label="Metric value", value=formatted),
            Fact(label="Source rows", value=fmt_int(len(df))),
        ]
    return ToolResult(
        updates={output: result},
        inputs=[table],
        outputs=[output],
        evidence=[
            EvidenceDraft(
                kind="metric",
                label=f"METRIC · {name}",
                summary=summary,
                facts=facts,
                details={
                    "definition": definition,
                    "kind": kind,
                    "numerator": numerator,
                    "denominator": denominator,
                    "value": value,
                    "condition": describe_where(args.get("where")),
                    "source_table": table,
                },
            )
        ],
        metric=metric,
        summary=f"Compute metric {name}",
    )


def _compose_report(
    args: dict[str, Any], tables: dict[str, pd.DataFrame], metrics: dict[str, MetricValue]
) -> ToolResult:
    title = args.get("title") or "Data task report"
    names = args.get("metric_names")
    if names is None:
        names = list(metrics.keys())
    if not isinstance(names, list):
        raise ToolError("invalid_args", "metric_names must be a list")
    selected = []
    for name in names:
        if isinstance(name, str) and name in metrics:
            selected.append(metrics[name])

    table_name = args.get("table")
    if table_name is not None and not isinstance(table_name, str):
        raise ToolError("invalid_args", "table must be a table name")
    table_frame = None
    if table_name is not None:
        if table_name not in tables:
            raise ToolError(
                "unknown_table",
                f"compose_report table {table_name!r} does not exist. Available: {sorted(tables)}",
            )
        table_frame = tables[table_name]
    elif not selected:
        # Older live plans sometimes pass a grouped output table through
        # metric_names. Accept that only when exactly one requested name is a table.
        candidates = [name for name in names if isinstance(name, str) and name in tables]
        if len(candidates) == 1:
            table_name = candidates[0]
            table_frame = tables[table_name]

    if not selected and table_frame is None:
        raise ToolError(
            "invalid_investigation",
            "compose_report has no computed metrics or table to report. "
            f"Computed metrics: {sorted(metrics)}. Requested names: {names}. "
            "Pass a computed metric name or a table output.",
        )
    summary = args.get("summary")
    if not summary:
        if selected and table_frame is not None:
            summary = (
                "Computed " + ", ".join(m.formatted for m in selected)
                + f"; reported table {table_name} with {fmt_int(len(table_frame))} rows."
            )
        elif selected:
            summary = "Computed " + ", ".join(m.formatted for m in selected) + "."
        else:
            summary = f"Reported table {table_name} with {fmt_int(len(table_frame))} rows."
    notes = args.get("notes") or []
    if not isinstance(notes, list):
        notes = [str(notes)]
    facts = [Fact(label=m.name, value=m.formatted) for m in selected]
    details: dict[str, Any] = {
        "title": str(title),
        "notes": notes,
        "metrics": [m.model_dump() for m in selected],
    }
    if table_frame is not None:
        facts.extend(
            [
                Fact(label="Table", value=str(table_name)),
                Fact(label="Rows", value=fmt_int(len(table_frame))),
                Fact(label="Columns", value=", ".join(str(column) for column in table_frame.columns[:8])),
            ]
        )
        details.update(
            {
                "table_name": table_name,
                "table_rows": int(len(table_frame)),
                "table_columns": [str(column) for column in table_frame.columns],
            }
        )
    report = Report(
        title=str(title),
        summary=str(summary),
        metrics=selected,
        table_name=table_name,
        table_rows=int(len(table_frame)) if table_frame is not None else None,
        notes=[str(n) for n in notes[:6]],
    )
    return ToolResult(
        evidence=[
            EvidenceDraft(
                kind="report",
                label="REPORT",
                summary=str(summary),
                facts=facts,
                details=details,
            )
        ],
        report=report,
        summary=f"Compose report: {title}",
    )


SANDBOX_COLUMNS = {"order_id", "customer_id", "order_status", "order_purchase_timestamp"}


def _sandbox_connection(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    if not p.exists():
        raise ToolError("sandbox_missing", "The disposable SQLite sandbox is not initialized for this run.")
    return sqlite3.connect(p)


def _sql_condition(where: Any) -> tuple[str, list[Any]]:
    if not where:
        return "1=1", []
    items = where if isinstance(where, list) else [where]
    clauses: list[str] = []
    params: list[Any] = []
    for item in items:
        if not isinstance(item, dict) or item.get("column") not in SANDBOX_COLUMNS:
            raise ToolError("invalid_condition", f"Sandbox condition must use {sorted(SANDBOX_COLUMNS)}")
        col = item["column"]
        op = item.get("op", "eq")
        value = item.get("value")
        if op == "eq":
            clauses.append(f'"{col}" = ?')
            params.append(value)
        elif op == "ne":
            clauses.append(f'"{col}" != ?')
            params.append(value)
        elif op == "in":
            if not isinstance(value, list) or not value:
                raise ToolError("invalid_condition", "in requires a non-empty list")
            clauses.append(f'"{col}" IN ({", ".join("?" for _ in value)})')
            params.extend(value)
        elif op == "not_in":
            if not isinstance(value, list) or not value:
                raise ToolError("invalid_condition", "not_in requires a non-empty list")
            clauses.append(f'"{col}" NOT IN ({", ".join("?" for _ in value)})')
            params.extend(value)
        elif op in {"gt", "gte", "lt", "lte"}:
            symbol = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
            clauses.append(f'"{col}" {symbol} ?')
            params.append(value)
        elif op == "is_null":
            clauses.append(f'"{col}" IS NULL')
        elif op == "not_null":
            clauses.append(f'"{col}" IS NOT NULL')
        else:
            raise ToolError("invalid_condition", f"Unsupported sandbox condition op {op!r}")
    return " AND ".join(clauses), params


def _sandbox_inspect(args: dict[str, Any], sandbox_path: str | None) -> ToolResult:
    if not sandbox_path:
        raise ToolError("sandbox_missing", "No sandbox database is attached to this run.")
    with _sandbox_connection(sandbox_path) as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM sandbox_orders").fetchone()[0])
        rows = conn.execute(
            "SELECT order_status, COUNT(*) AS n FROM sandbox_orders GROUP BY order_status ORDER BY n DESC"
        ).fetchall()
    distribution = {str(r[0]): int(r[1]) for r in rows}
    canceled = int(distribution.get("canceled", 0))
    metric = MetricValue(
        name="sandbox_rows",
        value=float(total),
        formatted=fmt_int(total),
        numerator=None,
        denominator=None,
        unit="number",
        definition="Current number of rows in the disposable SQLite sandbox table sandbox_orders.",
        extras={"canceled_rows": canceled, "status_distribution": distribution},
    )
    return ToolResult(
        evidence=[
            EvidenceDraft(
                kind="sandbox",
                label="SANDBOX · sandbox_orders",
                summary=(
                    f"Disposable sandbox has {fmt_int(total)} rows; "
                    f"{fmt_int(canceled)} have order_status = 'canceled'."
                ),
                facts=[
                    Fact(label="Rows", value=fmt_int(total)),
                    Fact(label="Canceled", value=fmt_int(canceled)),
                    Fact(label="Table", value="sandbox_orders (SQLite)"),
                ],
                details={"status_distribution": distribution, "database": str(sandbox_path)},
            )
        ],
        metric=metric,
        summary="Inspect disposable SQLite sandbox",
    )


def _sandbox_delete_rows(args: dict[str, Any], sandbox_path: str | None, preview: bool) -> ToolResult:
    if not sandbox_path:
        raise ToolError("sandbox_missing", "No sandbox database is attached to this run.")
    where = args.get("where")
    clause, params = _sql_condition(where)
    with _sandbox_connection(sandbox_path) as conn:
        total_before = int(conn.execute("SELECT COUNT(*) FROM sandbox_orders").fetchone()[0])
        affected = int(conn.execute(f"SELECT COUNT(*) FROM sandbox_orders WHERE {clause}", params).fetchone()[0])
        description = describe_where(where)
        facts = [
            Fact(label="Target", value="sandbox_orders (disposable SQLite)"),
            Fact(label="Condition", value=description),
            Fact(label="Affected rows", value=fmt_int(affected)),
            Fact(label="Rows before", value=fmt_int(total_before)),
        ]
        if preview:
            return ToolResult(
                requires_approval=True,
                preview={"affected_rows": affected, "total_before": total_before, "condition": description},
                evidence=[
                    EvidenceDraft(
                        kind="approval_preview",
                        label="RISKY WRITE · preview",
                        summary=(
                            f"Proposed DELETE on sandbox_orders would remove {fmt_int(affected)} of "
                            f"{fmt_int(total_before)} rows where {description}. No rows have been changed."
                        ),
                        facts=facts,
                        details={"sql_effect": f"DELETE FROM sandbox_orders WHERE {clause}", "params": params},
                    )
                ],
                summary=f"Proposed sandbox DELETE ({fmt_int(affected)} rows)",
            )
        deleted = int(conn.execute(f"DELETE FROM sandbox_orders WHERE {clause}", params).rowcount)
        conn.commit()
        total_after = int(conn.execute("SELECT COUNT(*) FROM sandbox_orders").fetchone()[0])
    return ToolResult(
        evidence=[
            EvidenceDraft(
                kind="sandbox_write",
                label="SANDBOX WRITE · executed",
                summary=(
                    f"Approved DELETE removed {fmt_int(deleted)} rows from sandbox_orders; "
                    f"{fmt_int(total_after)} rows remain."
                ),
                facts=facts + [Fact(label="Rows after", value=fmt_int(total_after))],
                details={"deleted_rows": deleted, "rows_after": total_after, "condition": description},
            )
        ],
        summary=f"Sandbox DELETE ({fmt_int(deleted)} rows)",
    )


SANDBOX_CUSTOMER_TABLE = "sandbox_customers"

_CUSTOMER_STATS_SQL = """
WITH latest AS (
    SELECT customer_id, MAX(order_purchase_timestamp) AS last_order
    FROM sandbox_orders
    GROUP BY customer_id
)
SELECT
    COUNT(*) AS total,
    COALESCE(SUM(CASE WHEN latest.customer_id IS NULL THEN 1 ELSE 0 END), 0) AS never_ordered,
    COALESCE(SUM(
        CASE WHEN latest.customer_id IS NULL THEN ?
             WHEN latest.last_order < ? THEN 1 ELSE 0 END
    ), 0) AS matching,
    COALESCE(SUM(CASE WHEN customers.archived = 1 THEN 1 ELSE 0 END), 0) AS archived,
    COALESCE(SUM(
        CASE WHEN customers.archived = 0 AND (
            CASE WHEN latest.customer_id IS NULL THEN ?
                 WHEN latest.last_order < ? THEN 1 ELSE 0 END
        ) = 1 THEN 1 ELSE 0 END
    ), 0) AS matching_unarchived
FROM sandbox_customers AS customers
LEFT JOIN latest ON customers.customer_id = latest.customer_id
"""


def _customer_cleanup_args(args: dict[str, Any]) -> tuple[str, int, bool]:
    table = args.get("table", SANDBOX_CUSTOMER_TABLE)
    if table != SANDBOX_CUSTOMER_TABLE:
        raise ToolError(
            "invalid_args",
            f"Customer cleanup can target only {SANDBOX_CUSTOMER_TABLE!r} in the disposable sandbox.",
        )
    reference = args.get("reference", "current_date")
    if reference not in {"current_date", "dataset_latest"}:
        raise ToolError(
            "invalid_args",
            "reference must be 'current_date' or 'dataset_latest'.",
        )
    try:
        months = int(args.get("months", 12))
    except (TypeError, ValueError) as exc:
        raise ToolError("invalid_args", "months must be an integer.") from exc
    if months < 1 or months > 120:
        raise ToolError("invalid_args", "months must be between 1 and 120.")
    include_never_ordered = bool(args.get("include_never_ordered", True))
    return reference, months, include_never_ordered


def _customer_order_range(conn: sqlite3.Connection) -> tuple[str, str]:
    row = conn.execute(
        "SELECT MIN(order_purchase_timestamp), MAX(order_purchase_timestamp) FROM sandbox_orders"
    ).fetchone()
    first = str(row[0]) if row and row[0] else ""
    last = str(row[1]) if row and row[1] else ""
    return first, last


def _customer_reference(
    reference_mode: str,
    months: int,
    last_order: str,
) -> tuple[str, str]:
    if reference_mode == "dataset_latest":
        if not last_order:
            raise ToolError(
                "sandbox_empty",
                "sandbox_orders has no purchase timestamps to anchor the reference date.",
            )
        reference = pd.Timestamp(last_order)
        reference_text = f"Dataset latest order ({reference:%Y-%m-%d %H:%M:%S})"
    else:
        reference = pd.Timestamp(date.today())
        reference_text = f"Current date ({reference:%Y-%m-%d})"
    cutoff = reference - pd.DateOffset(months=months)
    cutoff_text = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    return reference_text, cutoff_text


def _customer_stats(
    conn: sqlite3.Connection,
    include_never_ordered: bool,
    cutoff_text: str,
) -> dict[str, Any]:
    include_flag = 1 if include_never_ordered else 0
    try:
        row = conn.execute(
            _CUSTOMER_STATS_SQL,
            (include_flag, cutoff_text, include_flag, cutoff_text),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise ToolError(
            "sandbox_schema",
            "The disposable sandbox does not contain sandbox_customers. Start a new run.",
        ) from exc
    return {
        "customers_total": int(row[0]),
        "never_ordered": int(row[1]),
        "matching": int(row[2]),
        "archived": int(row[3]),
        "matching_unarchived": int(row[4]),
    }


def _customer_scope_details(
    *,
    path: str | Path,
    reference_mode: str,
    months: int,
    include_never_ordered: bool,
    reference_text: str,
    cutoff_text: str,
    first_order: str,
    last_order: str,
    stats: dict[str, Any],
) -> dict[str, Any]:
    return {
        "target_table": SANDBOX_CUSTOMER_TABLE,
        "reference_mode": reference_mode,
        "declared_reference_date": reference_text,
        "cutoff": cutoff_text,
        "months": months,
        "include_never_ordered": include_never_ordered,
        "dataset_first_order": first_order,
        "dataset_last_order": last_order,
        "customers_total": stats["customers_total"],
        "never_ordered": stats["never_ordered"],
        "matching_customers": stats["matching"],
        "already_archived": stats["archived"],
        "matching_unarchived": stats["matching_unarchived"],
        "predicate": (
            "latest order before cutoff"
            + ("; customers with no orders included" if include_never_ordered else "; customers with no orders excluded")
        ),
        "database": str(path),
    }


def _customer_scope_facts(
    stats: dict[str, Any],
    reference_text: str,
    cutoff_text: str,
    first_order: str,
    last_order: str,
    include_never_ordered: bool,
) -> list[Fact]:
    never_policy = "included" if include_never_ordered else "excluded"
    order_range = (
        f"{first_order[:10]} → {last_order[:10]}" if first_order and last_order else "—"
    )
    return [
        Fact(label="Target", value=f"{SANDBOX_CUSTOMER_TABLE} (disposable SQLite)"),
        Fact(label="Declared reference", value=reference_text),
        Fact(label="Cutoff", value=cutoff_text),
        Fact(
            label="Predicate",
            value=f"latest order before cutoff; no-order customers {never_policy}",
        ),
        Fact(label="Order range", value=order_range),
        Fact(label="Matching rows", value=fmt_int(stats["matching"])),
        Fact(label="Customers before", value=fmt_int(stats["customers_total"])),
        Fact(
            label="Never ordered",
            value=f"{fmt_int(stats['never_ordered'])} ({never_policy})",
        ),
    ]


def _sandbox_customer_preview(args: dict[str, Any], sandbox_path: str | None) -> ToolResult:
    if not sandbox_path:
        raise ToolError("sandbox_missing", "No sandbox database is attached to this run.")
    reference_mode, months, include_never_ordered = _customer_cleanup_args(args)
    with _sandbox_connection(sandbox_path) as conn:
        first_order, last_order = _customer_order_range(conn)
        reference_text, cutoff_text = _customer_reference(
            reference_mode, months, last_order
        )
        stats = _customer_stats(conn, include_never_ordered, cutoff_text)
    details = _customer_scope_details(
        path=sandbox_path,
        reference_mode=reference_mode,
        months=months,
        include_never_ordered=include_never_ordered,
        reference_text=reference_text,
        cutoff_text=cutoff_text,
        first_order=first_order,
        last_order=last_order,
        stats=stats,
    )
    matching = stats["matching"]
    summary = (
        f"Read-only preview: {fmt_int(matching)} of {fmt_int(stats['customers_total'])} "
        f"customer rows match the inactivity predicate (reference {reference_text}, "
        f"cutoff {cutoff_text}). No rows have been changed."
    )
    metric = MetricValue(
        name="inactive_customers",
        value=float(matching),
        formatted=fmt_int(matching),
        unit="number",
        definition=(
            "Customer rows whose latest order predates the resolved 12-month cutoff; "
            f"customers with no orders are {('included' if include_never_ordered else 'excluded')}."
        ),
        extras={
            "reference_mode": reference_mode,
            "declared_reference_date": reference_text,
            "cutoff": cutoff_text,
            "dataset_last_order": last_order,
            "never_ordered": stats["never_ordered"],
        },
    )
    return ToolResult(
        evidence=[
            EvidenceDraft(
                kind="sandbox_customer_preview",
                label="SANDBOX PREVIEW · customer inactivity",
                summary=summary,
                facts=_customer_scope_facts(
                    stats,
                    reference_text,
                    cutoff_text,
                    first_order,
                    last_order,
                    include_never_ordered,
                ),
                details=details,
            )
        ],
        metric=metric,
        summary=f"Preview inactive customers ({fmt_int(matching)} rows)",
    )


def _sandbox_delete_inactive_customers(
    args: dict[str, Any], sandbox_path: str | None, preview: bool
) -> ToolResult:
    if not sandbox_path:
        raise ToolError("sandbox_missing", "No sandbox database is attached to this run.")
    reference_mode, months, include_never_ordered = _customer_cleanup_args(args)
    with _sandbox_connection(sandbox_path) as conn:
        first_order, last_order = _customer_order_range(conn)
        reference_text, cutoff_text = _customer_reference(
            reference_mode, months, last_order
        )
        stats = _customer_stats(conn, include_never_ordered, cutoff_text)
        details = _customer_scope_details(
            path=sandbox_path,
            reference_mode=reference_mode,
            months=months,
            include_never_ordered=include_never_ordered,
            reference_text=reference_text,
            cutoff_text=cutoff_text,
            first_order=first_order,
            last_order=last_order,
            stats=stats,
        )
        facts = _customer_scope_facts(
            stats,
            reference_text,
            cutoff_text,
            first_order,
            last_order,
            include_never_ordered,
        )
        if preview:
            return ToolResult(
                requires_approval=True,
                preview={
                    "affected_rows": stats["matching"],
                    "total_before": stats["customers_total"],
                    "reference": reference_text,
                    "cutoff": cutoff_text,
                },
                evidence=[
                    EvidenceDraft(
                        kind="approval_preview",
                        label="RISKY WRITE · preview",
                        summary=(
                            f"Proposed DELETE on {SANDBOX_CUSTOMER_TABLE} would remove "
                            f"{fmt_int(stats['matching'])} of {fmt_int(stats['customers_total'])} "
                            f"customer rows where the latest order predates {cutoff_text}. "
                            "No rows have been changed."
                        ),
                        facts=facts,
                        details={
                            **details,
                            "sql_effect": (
                                "DELETE FROM sandbox_customers WHERE customer_id IN "
                                "(customers whose latest order precedes the cutoff)"
                            ),
                        },
                    )
                ],
                summary=f"Proposed customer DELETE ({fmt_int(stats['matching'])} rows)",
            )
        include_flag = 1 if include_never_ordered else 0
        deleted = int(
            conn.execute(
                """
                DELETE FROM sandbox_customers
                WHERE customer_id IN (
                    SELECT customers.customer_id
                    FROM sandbox_customers AS customers
                    LEFT JOIN (
                        SELECT customer_id, MAX(order_purchase_timestamp) AS last_order
                        FROM sandbox_orders
                        GROUP BY customer_id
                    ) AS latest ON customers.customer_id = latest.customer_id
                    WHERE (latest.customer_id IS NULL AND ? = 1)
                       OR (latest.last_order < ?)
                )
                """,
                (include_flag, cutoff_text),
            ).rowcount
        )
        conn.commit()
        total_after = int(conn.execute("SELECT COUNT(*) FROM sandbox_customers").fetchone()[0])
    return ToolResult(
        evidence=[
            EvidenceDraft(
                kind="sandbox_write",
                label="SANDBOX WRITE · executed",
                summary=(
                    f"Approved DELETE removed {fmt_int(deleted)} customer rows from "
                    f"{SANDBOX_CUSTOMER_TABLE}; {fmt_int(total_after)} customer rows remain. "
                    "The read-only source CSVs were not changed."
                ),
                facts=[*facts, Fact(label="Deleted rows", value=fmt_int(deleted)), Fact(label="Customers after", value=fmt_int(total_after))],
                details={
                    **details,
                    "deleted_rows": deleted,
                    "customers_after": total_after,
                    "sql_effect": (
                        "DELETE FROM sandbox_customers WHERE customer_id IN "
                        "(customers whose latest order precedes the cutoff)"
                    ),
                },
            )
        ],
        summary=f"Customer DELETE ({fmt_int(deleted)} rows)",
    )


def _sandbox_archive_inactive_customers(
    args: dict[str, Any], sandbox_path: str | None, preview: bool
) -> ToolResult:
    if not sandbox_path:
        raise ToolError("sandbox_missing", "No sandbox database is attached to this run.")
    reference_mode, months, include_never_ordered = _customer_cleanup_args(args)
    with _sandbox_connection(sandbox_path) as conn:
        first_order, last_order = _customer_order_range(conn)
        reference_text, cutoff_text = _customer_reference(
            reference_mode, months, last_order
        )
        stats = _customer_stats(conn, include_never_ordered, cutoff_text)
        details = _customer_scope_details(
            path=sandbox_path,
            reference_mode=reference_mode,
            months=months,
            include_never_ordered=include_never_ordered,
            reference_text=reference_text,
            cutoff_text=cutoff_text,
            first_order=first_order,
            last_order=last_order,
            stats=stats,
        )
        if preview:
            return ToolResult(
                preview={
                    "affected_rows": stats["matching_unarchived"],
                    "total_before": stats["customers_total"],
                    "reference": reference_text,
                    "cutoff": cutoff_text,
                },
                evidence=[
                    EvidenceDraft(
                        kind="archive_preview",
                        label="ARCHIVE · preview",
                        summary=(
                            f"Proposed archival update would mark {fmt_int(stats['matching_unarchived'])} "
                            f"of {fmt_int(stats['customers_total'])} customer rows as archived. "
                            "No rows would be removed and no rows have been changed."
                        ),
                        facts=[
                            Fact(label="Target", value=f"{SANDBOX_CUSTOMER_TABLE} (disposable SQLite)"),
                            Fact(label="Declared reference", value=reference_text),
                            Fact(label="Cutoff", value=cutoff_text),
                            Fact(label="Would archive", value=fmt_int(stats["matching_unarchived"])),
                            Fact(label="Already archived", value=fmt_int(stats["archived"])),
                            Fact(label="Rows removed", value="0"),
                        ],
                        details={
                            **details,
                            "sql_effect": (
                                "UPDATE sandbox_customers SET archived = 1 "
                                "WHERE archived = 0 AND customer_id IN "
                                "(customers whose latest order precedes the cutoff)"
                            ),
                        },
                    )
                ],
                summary=f"Proposed customer archival ({fmt_int(stats['matching_unarchived'])} rows)",
            )
        include_flag = 1 if include_never_ordered else 0
        archived_now = int(
            conn.execute(
                """
                UPDATE sandbox_customers
                SET archived = 1, archived_at = ?
                WHERE archived = 0
                  AND customer_id IN (
                      SELECT customers.customer_id
                      FROM sandbox_customers AS customers
                      LEFT JOIN (
                          SELECT customer_id, MAX(order_purchase_timestamp) AS last_order
                          FROM sandbox_orders
                          GROUP BY customer_id
                      ) AS latest ON customers.customer_id = latest.customer_id
                      WHERE (latest.customer_id IS NULL AND ? = 1)
                         OR (latest.last_order < ?)
                  )
                """,
                (utcnow(), include_flag, cutoff_text),
            ).rowcount
        )
        conn.commit()
        total_after = int(conn.execute("SELECT COUNT(*) FROM sandbox_customers").fetchone()[0])
        archived_after = int(
            conn.execute("SELECT COUNT(*) FROM sandbox_customers WHERE archived = 1").fetchone()[0]
        )
    metric = MetricValue(
        name="customers_archived",
        value=float(archived_after),
        formatted=fmt_int(archived_after),
        unit="number",
        definition=(
            "Customer rows marked archived = 1 in the disposable sandbox after the "
            "non-destructive inactivity cleanup."
        ),
        extras={
            "archived_now": archived_now,
            "customers_total": total_after,
            "reference_mode": reference_mode,
            "declared_reference_date": reference_text,
            "cutoff": cutoff_text,
        },
    )
    return ToolResult(
        evidence=[
            EvidenceDraft(
                kind="sandbox_archive",
                label="SANDBOX ARCHIVE · executed",
                summary=(
                    f"Archived {fmt_int(archived_now)} customer rows by setting archived = 1; "
                    f"{fmt_int(archived_after)} of {fmt_int(total_after)} customer rows are archived. "
                    "No customer records were removed."
                ),
                facts=[
                    Fact(label="Target", value=f"{SANDBOX_CUSTOMER_TABLE} (disposable SQLite)"),
                    Fact(label="Declared reference", value=reference_text),
                    Fact(label="Cutoff", value=cutoff_text),
                    Fact(label="Archived now", value=fmt_int(archived_now)),
                    Fact(label="Archived total", value=fmt_int(archived_after)),
                    Fact(label="Customers before", value=fmt_int(stats["customers_total"])),
                    Fact(label="Customers after", value=fmt_int(total_after)),
                    Fact(label="Rows removed", value="0"),
                ],
                details={
                    **details,
                    "archived_now": archived_now,
                    "archived_total": archived_after,
                    "customers_after": total_after,
                    "rows_removed": 0,
                    "sql_effect": (
                        "UPDATE sandbox_customers SET archived = 1, archived_at = ? "
                        "WHERE archived = 0 AND customer_id IN "
                        "(customers whose latest order precedes the cutoff)"
                    ),
                },
            )
        ],
        metric=metric,
        summary=f"Customer archival ({fmt_int(archived_now)} rows)",
    )


def _sandbox_customer_audit(
    args: dict[str, Any], sandbox_path: str | None, dataset
) -> ToolResult:
    if not sandbox_path:
        raise ToolError("sandbox_missing", "No sandbox database is attached to this run.")
    reference_mode, months, include_never_ordered = _customer_cleanup_args(args)
    with _sandbox_connection(sandbox_path) as conn:
        first_order, last_order = _customer_order_range(conn)
        reference_text, cutoff_text = _customer_reference(
            reference_mode, months, last_order
        )
        stats = _customer_stats(conn, include_never_ordered, cutoff_text)
    source_rows = int(len(dataset.table("customers")))
    sandbox_rows = stats["customers_total"]
    removed = source_rows - sandbox_rows
    archived = stats["archived"]
    active = sandbox_rows - archived
    details = _customer_scope_details(
        path=sandbox_path,
        reference_mode=reference_mode,
        months=months,
        include_never_ordered=include_never_ordered,
        reference_text=reference_text,
        cutoff_text=cutoff_text,
        first_order=first_order,
        last_order=last_order,
        stats=stats,
    )
    details.update(
        {
            "source_customer_rows": source_rows,
            "sandbox_customer_rows": sandbox_rows,
            "rows_removed": removed,
            "archived_rows": archived,
            "active_rows": active,
        }
    )
    if removed == 0:
        summary = (
            f"Source and sandbox customer rows both contain {fmt_int(source_rows)} rows; "
            f"{fmt_int(archived)} rows are archived and {fmt_int(removed)} customer records were removed."
        )
    else:
        summary = (
            f"Sandbox customer rows: {fmt_int(sandbox_rows)} of {fmt_int(source_rows)} source rows "
            f"({fmt_int(removed)} removed from the disposable copy); {fmt_int(archived)} archived."
        )
    metric = MetricValue(
        name="customers_remaining",
        value=float(sandbox_rows),
        formatted=fmt_int(sandbox_rows),
        unit="number",
        definition=(
            "Customer rows remaining in the disposable sandbox after the cleanup decision. "
            "Archived rows remain present and are counted here."
        ),
        extras={
            "source_customer_rows": source_rows,
            "sandbox_customer_rows": sandbox_rows,
            "rows_removed": removed,
            "archived_rows": archived,
            "active_rows": active,
        },
    )
    return ToolResult(
        evidence=[
            EvidenceDraft(
                kind="sandbox_audit",
                label="SANDBOX AUDIT · sandbox_customers",
                summary=summary,
                facts=[
                    Fact(label="Source customer rows", value=fmt_int(source_rows)),
                    Fact(label="Sandbox customer rows", value=fmt_int(sandbox_rows)),
                    Fact(label="Rows removed", value=fmt_int(removed)),
                    Fact(label="Archived", value=fmt_int(archived)),
                    Fact(label="Active (not archived)", value=fmt_int(active)),
                    Fact(label="Inactive by cutoff", value=fmt_int(stats["matching"])),
                    Fact(label="Declared reference", value=reference_text),
                    Fact(label="Cutoff", value=cutoff_text),
                ],
                details=details,
            )
        ],
        metric=metric,
        summary="Audit customer rows and archived state",
    )


REQUIRES_APPROVAL = {"sandbox_delete_rows", "sandbox_delete_inactive_customers"}


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "inspect_schema",
        "args": {"table": "orders|customers|order_items|order_payments|products", "columns": "optional list"},
        "read_only": True,
    },
    {
        "name": "inspect_data_dictionary",
        "args": {"table": "optional table name"},
        "read_only": True,
    },
    {"name": "profile_column", "args": {"table": "str", "column": "str", "top_n": "optional 1-10"}, "read_only": True},
    {
        "name": "sample_records",
        "args": {"table": "str", "n": "optional 1-10", "columns": "optional list"},
        "read_only": True,
    },
    {
        "name": "filter_rows",
        "args": {
            "table": "str",
            "where": "[{column, op: eq|ne|gt|gte|lt|lte|in|not_in|is_null|not_null|contains, value}]",
            "output": "new table name",
        },
    },
    {
        "name": "join_tables",
        "args": {
            "left": "table",
            "right": "table",
            "left_on": "str or list",
            "right_on": "str or list",
            "how": "inner|left|right|outer",
            "output": "new table name",
        },
    },
    {
        "name": "group_and_aggregate",
        "args": {
            "table": "table",
            "by": "list of columns",
            "date_part": "optional {column, alias, unit: month|quarter|year|day}",
            "aggregations": "[{column: name or '*', func: sum|count|mean|min|max|nunique, alias}]",
            "output": "new table name",
        },
    },
    {
        "name": "compute_metric",
        "args": {
            "table": "table",
            "name": "metric name",
            "kind": "share_of_rows|sum|mean",
            "where": "optional condition list for share_of_rows",
            "column": "required for sum/mean",
            "unit": "percent|currency|number",
            "definition": "explicit metric definition",
            "output": "new table name",
        },
    },
    {
        "name": "compose_report",
        "args": {
            "title": "str",
            "metric_names": "optional list of computed metric names",
            "table": "optional output table for a table-shaped result",
            "summary": "concise explicit summary",
            "notes": "optional list",
        },
    },
    {
        "name": "sandbox_customer_preview",
        "args": {
            "table": "sandbox_customers",
            "reference": "current_date|dataset_latest",
            "months": "positive integer",
            "include_never_ordered": "bool",
        },
        "read_only": True,
        "sandbox_only": True,
    },
    {
        "name": "sandbox_delete_inactive_customers",
        "args": {
            "table": "sandbox_customers",
            "reference": "current_date|dataset_latest",
            "months": "positive integer",
            "include_never_ordered": "bool",
        },
        "sandbox_only": True,
        "destructive": True,
    },
    {
        "name": "sandbox_archive_inactive_customers",
        "args": {
            "table": "sandbox_customers",
            "reference": "current_date|dataset_latest",
            "months": "positive integer",
            "include_never_ordered": "bool",
        },
        "sandbox_only": True,
    },
    {
        "name": "sandbox_customer_audit",
        "args": {
            "table": "sandbox_customers",
            "reference": "current_date|dataset_latest",
            "months": "positive integer",
            "include_never_ordered": "bool",
        },
        "read_only": True,
        "sandbox_only": True,
    },
]


def execute_tool(
    tool: str,
    args: dict[str, Any],
    tables: dict[str, pd.DataFrame],
    dataset,
    metrics: dict[str, MetricValue] | None = None,
    sandbox_path: str | None = None,
    preview: bool = False,
) -> ToolResult:
    if not isinstance(args, dict):
        raise ToolError("invalid_args", "operation args must be an object")
    metrics = metrics or {}
    if tool == "inspect_schema":
        return _inspect_schema(args, dataset)
    if tool == "inspect_data_dictionary":
        return _inspect_dictionary(args, dataset)
    if tool == "profile_column":
        return _profile_column(args, dataset)
    if tool == "sample_records":
        return _sample_records(args, tables)
    if tool == "filter_rows":
        return _filter_rows(args, tables)
    if tool == "join_tables":
        return _join_tables(args, tables)
    if tool == "group_and_aggregate":
        return _group_and_aggregate(args, tables)
    if tool == "compute_metric":
        return _compute_metric(args, tables)
    if tool == "compose_report":
        return _compose_report(args, tables, metrics)
    if tool == "sandbox_inspect":
        return _sandbox_inspect(args, sandbox_path)
    if tool == "sandbox_delete_rows":
        return _sandbox_delete_rows(args, sandbox_path, preview=preview)
    if tool == "sandbox_customer_preview":
        return _sandbox_customer_preview(args, sandbox_path)
    if tool == "sandbox_delete_inactive_customers":
        return _sandbox_delete_inactive_customers(args, sandbox_path, preview=preview)
    if tool == "sandbox_archive_inactive_customers":
        return _sandbox_archive_inactive_customers(args, sandbox_path, preview=preview)
    if tool == "sandbox_customer_audit":
        return _sandbox_customer_audit(args, sandbox_path, dataset)
    raise ToolError("unknown_tool", f"Unsupported tool {tool!r}")


OPERATION_SUMMARIES = {
    "filter_rows": "Filter rows",
    "join_tables": "Join tables",
    "group_and_aggregate": "Group and aggregate",
    "compute_metric": "Compute metric",
    "compose_report": "Compose report",
    "inspect_schema": "Inspect schema",
    "inspect_data_dictionary": "Inspect data dictionary",
    "profile_column": "Profile column",
    "sample_records": "Sample records",
    "sandbox_inspect": "Inspect sandbox",
    "sandbox_delete_rows": "Delete sandbox rows",
    "sandbox_customer_preview": "Preview inactive customers",
    "sandbox_delete_inactive_customers": "Delete inactive customer rows",
    "sandbox_archive_inactive_customers": "Archive inactive customer rows",
    "sandbox_customer_audit": "Audit customer rows",
}
