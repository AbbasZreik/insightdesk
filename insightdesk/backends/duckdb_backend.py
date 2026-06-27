"""
DuckDB backend — the embedded adapter that powers the *public live demo*.

Why DuckDB for the demo: zero external services, single-file database, runs
anywhere (Hugging Face Spaces / Render free tier) without a cluster to keep
alive during judging. Same AggregationSpec contract as the Elasticsearch
adapter, so the agent code is identical regardless of which backend is wired in.
"""
from __future__ import annotations

from typing import Any

import duckdb

from .base import (
    AggregationBackend,
    AggregationSpec,
    Filter,
    SchemaInfo,
    validate_spec,
)

# Map our granularity tokens to DuckDB date_trunc units.
_TRUNC = {"day": "day", "week": "week", "month": "month"}

# Metrics the agent may aggregate, with a sensible default agg for each.
_METRICS: dict[str, str] = {"cost": "sum", "message_count": "sum"}
_DIMENSIONS = ["client_id", "region", "product", "plan", "status", "client_name"]


class DuckDBBackend(AggregationBackend):
    def __init__(self, db_path: str):
        self.con = duckdb.connect(db_path, read_only=True)

    # -- schema ------------------------------------------------------------
    def get_schema(self) -> SchemaInfo:
        dim_values: dict[str, list[str]] = {}
        # region/product live on the fact table; plan/status on the clients dim.
        for d, tbl in (("region", "events"), ("product", "events"),
                       ("plan", "clients"), ("status", "clients")):
            rows = self.con.execute(
                f"SELECT DISTINCT {d} FROM {tbl} ORDER BY 1"
            ).fetchall()
            dim_values[d] = [r[0] for r in rows]
        return SchemaInfo(
            table="events",
            time_field="event_date",
            metrics=_METRICS,                      # type: ignore[arg-type]
            dimensions=_DIMENSIONS,
            dimension_values=dim_values,
        )

    # -- query -------------------------------------------------------------
    def run_aggregation(self, spec: AggregationSpec) -> list[dict[str, Any]]:
        schema = self.get_schema()
        validate_spec(spec, schema)            # guardrail BEFORE any SQL runs
        sql, params = self._build_sql(spec)
        cur = self.con.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # -- spec -> SQL -------------------------------------------------------
    def _build_sql(self, spec: AggregationSpec) -> tuple[str, list[Any]]:
        params: list[Any] = []

        # We always query a view that joins the client dimension so that
        # plan / status / client_name are groupable alongside event fields.
        src = (
            "(SELECT e.*, c.plan, c.status, c.client_name "
            "FROM events e LEFT JOIN clients c USING (client_id))"
        )

        select_parts: list[str] = []
        group_parts: list[str] = []

        for g in spec.group_by:
            if g == "__time__":
                unit = _TRUNC[spec.granularity or "month"]
                select_parts.append(f"date_trunc('{unit}', event_date) AS bucket")
                group_parts.append("bucket")
            else:
                select_parts.append(g)
                group_parts.append(g)

        # The aggregated measure.
        if spec.agg == "count":
            measure = "COUNT(*)"
        else:
            measure = f"{spec.agg.upper()}({spec.metric})"
        select_parts.append(f"{measure} AS value")

        sql = f"SELECT {', '.join(select_parts)} FROM {src} AS v"

        where, wparams = self._build_where(spec)
        params.extend(wparams)
        if where:
            sql += f" WHERE {where}"

        if group_parts:
            sql += f" GROUP BY {', '.join(group_parts)}"

        order = spec.order_by or ("value" if group_parts else None)
        if order:
            direction = "DESC" if spec.order_desc else "ASC"
            sql += f" ORDER BY {order} {direction}"

        if spec.limit:
            sql += f" LIMIT {int(spec.limit)}"
        return sql, params

    def _build_where(self, spec: AggregationSpec) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if spec.time_start:
            clauses.append("event_date >= ?")
            params.append(spec.time_start)
        if spec.time_end:
            clauses.append("event_date <= ?")
            params.append(spec.time_end)
        for f in spec.filters:
            clauses.append(_filter_sql(f, params))
        return " AND ".join(clauses), params


def _filter_sql(f: Filter, params: list[Any]) -> str:
    if f.op == "eq":
        params.append(f.value)
        return f"{f.field} = ?"
    if f.op == "in":
        vals = list(f.value)
        params.extend(vals)
        placeholders = ", ".join("?" for _ in vals)
        return f"{f.field} IN ({placeholders})"
    if f.op == "gte":
        params.append(f.value)
        return f"{f.field} >= ?"
    if f.op == "lte":
        params.append(f.value)
        return f"{f.field} <= ?"
    raise ValueError(f"Unsupported filter op {f.op!r}")
