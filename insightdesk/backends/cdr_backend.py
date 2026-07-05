"""
DuckDB backend over the CDR table — powers the reporting agent.

Single denormalised table `cdr`, so no joins. Same AggregationSpec contract as
the rest of the system, so the agent, validator, skills, narrator, and tracing
all work unchanged — only the schema differs.

Metrics include a derived delivery rate: metric "delivered" with agg "avg"
returns delivered/total (0..1). margin is sum(profit).
"""
from __future__ import annotations

from typing import Any

import duckdb

from .base import (
    AggregationBackend, AggregationSpec, Filter, SchemaInfo, validate_spec,
)

_TRUNC = {"minute": "minute", "hour": "hour", "day": "day",
          "week": "week", "month": "month"}

# metric name -> typical agg (count uses COUNT(*))
_METRICS: dict[str, str] = {
    "message_count": "count", "cost": "sum", "profit": "sum", "rate": "sum",
    "delivered": "avg", "latency_sec": "avg",
}
_DIMENSIONS = ["client_id", "client_name", "vendor_id", "vendor_name",
               "country_name", "operator_name", "sender", "content_type",
               "delivery_status"]
_VALUE_DIMS = ["country_name", "operator_name", "content_type", "delivery_status",
               "client_name", "vendor_name"]


class CDRBackend(AggregationBackend):
    def __init__(self, db_path: str):
        self.con = duckdb.connect(db_path, read_only=True)

    def get_schema(self) -> SchemaInfo:
        dim_values: dict[str, list[str]] = {}
        for d in _VALUE_DIMS:
            rows = self.con.execute(f"SELECT DISTINCT {d} FROM cdr ORDER BY 1").fetchall()
            dim_values[d] = [r[0] for r in rows]
        return SchemaInfo(table="cdr", time_field="created_ts", metrics=_METRICS,  # type: ignore[arg-type]
                          dimensions=_DIMENSIONS, dimension_values=dim_values)

    def run_aggregation(self, spec: AggregationSpec) -> list[dict[str, Any]]:
        schema = self.get_schema()
        validate_spec(spec, schema)
        sql, params = self._build_sql(spec)
        cur = self.con.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _build_sql(self, spec: AggregationSpec) -> tuple[str, list[Any]]:
        select, group = [], []
        for g in spec.group_by:
            if g == "__time__":
                unit = _TRUNC[spec.granularity or "hour"]
                select.append(f"date_trunc('{unit}', created_ts) AS bucket")
                group.append("bucket")
            else:
                select.append(g)
                group.append(g)

        if spec.agg == "count" or spec.metric == "message_count":
            measure = "COUNT(*)"
        else:
            measure = f"{spec.agg.upper()}({spec.metric})"
        select.append(f"{measure} AS value")

        sql = f"SELECT {', '.join(select)} FROM cdr"
        where, params = self._where(spec)
        if where:
            sql += f" WHERE {where}"
        if group:
            sql += f" GROUP BY {', '.join(group)}"
        order = spec.order_by or ("value" if group else None)
        if order:
            sql += f" ORDER BY {order} {'DESC' if spec.order_desc else 'ASC'}"
        if spec.limit:
            sql += f" LIMIT {int(spec.limit)}"
        return sql, params

    def _where(self, spec: AggregationSpec) -> tuple[str, list[Any]]:
        clauses, params = [], []
        if spec.time_start:
            clauses.append("created_ts >= ?"); params.append(spec.time_start)
        if spec.time_end:
            clauses.append("created_ts <= ?"); params.append(spec.time_end)
        for f in spec.filters:
            clauses.append(_filter_sql(f, params))
        return " AND ".join(clauses), params


def _filter_sql(f: Filter, params: list[Any]) -> str:
    if f.op == "eq":
        params.append(f.value); return f"{f.field} = ?"
    if f.op == "in":
        vals = list(f.value); params.extend(vals)
        return f"{f.field} IN ({', '.join('?' for _ in vals)})"
    if f.op in ("gte", "lte"):
        params.append(f.value); return f"{f.field} {'>=' if f.op == 'gte' else '<='} ?"
    raise ValueError(f"Unsupported op {f.op!r}")
