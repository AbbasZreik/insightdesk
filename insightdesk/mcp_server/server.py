"""
SMS Traffic MCP server (read-only).

Exposes the CDR reporting + monitoring system as Model Context Protocol tools so
any MCP client (Claude, Gemini CLI, an ADK agent) can *read* traffic state and
the ambient agent's escalations. By design there are NO action tools here: the
agent can see and recommend, but blocking/dismissing a route is always a human
action in the portal. That bounds the agent's blast radius to reads.

Tools:
  get_schema           metrics + dimensions available for reporting
  run_report           structured aggregation over CDR (deterministic, no LLM)
  list_escalations     open escalations the ambient agent has raised
  get_route_detail     recent traffic for one route (context before a human acts)
  get_window_summary   overall live-traffic health (volume, delivery rate)

Run:  python -m insightdesk.mcp_server.server         (stdio transport)
"""
from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..agent.anomaly import detect_anomalies
from ..backends.base import AggregationSpec, Filter
from ..backends.cdr_backend import CDRBackend
from ..monitor.engine import MonitoringAgent
from ..monitor.stream import CDRStream

DB = os.environ.get("INSIGHTDESK_DB", "insightdesk/data/cdr.duckdb")
PARQUET = os.environ.get("INSIGHTDESK_PARQUET", "insightdesk/data/cdr.parquet")

mcp = FastMCP("insightdesk-sms")

_backend: CDRBackend | None = None
_monitor: MonitoringAgent | None = None


def _be() -> CDRBackend:
    global _backend
    if _backend is None:
        _backend = CDRBackend(DB)
    return _backend


def _mon() -> MonitoringAgent:
    """Build the ambient agent once and replay the whole stream so its
    escalations reflect a full run (same as the portal after streaming)."""
    global _monitor
    if _monitor is None:
        agent = MonitoringAgent()
        stream = CDRStream(PARQUET, batch_min=5)
        while stream.has_next():
            batch = stream.next_batch()
            if not batch.empty:
                agent.ingest(batch)
                agent.evaluate()
        _monitor = agent
    return _monitor


def _max_ts() -> str:
    return str(_be().con.execute("SELECT max(created_ts) FROM cdr").fetchone()[0])


# -- tool logic (plain functions, unit-testable) ---------------------------

def _get_schema() -> dict[str, Any]:
    s = _be().get_schema()
    return {"table": s.table, "time_field": s.time_field,
            "metrics": s.metrics, "dimensions": s.dimensions,
            "dimension_values": s.dimension_values,
            "note": "delivery rate = metric 'delivered' with agg 'avg'; "
                    "'message_count' counts rows; 'profit' is margin."}


def _run_report(metric: str, agg: str = "sum", group_by: list[str] | None = None,
                filters: list[dict] | None = None, granularity: str | None = None,
                limit: int | None = 50) -> dict[str, Any]:
    gb = list(group_by or [])
    fs = [Filter(f["field"], f.get("op", "eq"), f["value"]) for f in (filters or [])]
    spec = AggregationSpec(metric=metric, agg=agg, group_by=gb, filters=fs,
                           granularity=granularity, limit=limit)
    rows = _be().run_aggregation(spec)          # validate_spec runs inside
    anomalies = detect_anomalies(rows, gb)
    return {"spec": spec.to_dict(), "row_count": len(rows),
            "rows": rows, "anomalies": anomalies}


def _list_escalations() -> dict[str, Any]:
    store = _mon().store
    return {"open": [e.to_dict() for e in store.open()], "counts": store.counts()}


def _get_route_detail(client_id: str | None = None, country_name: str | None = None,
                      operator_name: str | None = None, vendor_name: str | None = None,
                      minutes: int = 30) -> dict[str, Any]:
    spec_filters = []
    for field, val in [("client_id", client_id), ("country_name", country_name),
                       ("operator_name", operator_name), ("vendor_name", vendor_name)]:
        if val:
            spec_filters.append(Filter(field, "eq", val))
    if not spec_filters:
        return {"error": "specify at least one of client_id, country_name, "
                         "operator_name, vendor_name"}
    # recent window
    import pandas as pd
    start = str(pd.to_datetime(_max_ts()) - pd.Timedelta(minutes=minutes))
    out: dict[str, Any] = {"window_minutes": minutes, "filters":
                           [vars(f) for f in spec_filters]}
    for label, metric, agg in [("messages", "message_count", "count"),
                               ("delivery_rate", "delivered", "avg"),
                               ("profit", "profit", "sum")]:
        spec = AggregationSpec(metric=metric, agg=agg, group_by=[],
                               filters=spec_filters, time_start=start, limit=1)
        rows = _be().run_aggregation(spec)
        out[label] = round(rows[0]["value"], 4) if rows and rows[0].get("value") is not None else None
    return out


def _get_window_summary() -> dict[str, Any]:
    return _mon().window_summary()


# -- MCP tool registrations -------------------------------------------------

@mcp.tool()
def get_schema() -> dict:
    """Return the CDR reporting schema: metrics, dimensions, and their values."""
    return _get_schema()


@mcp.tool()
def run_report(metric: str, agg: str = "sum", group_by: list[str] | None = None,
               filters: list[dict] | None = None, granularity: str | None = None,
               limit: int | None = 50) -> dict:
    """Run a structured, read-only aggregation over the CDR data and flag
    statistical anomalies. metric e.g. 'delivered' (avg = delivery rate),
    'profit' (sum), 'message_count'. group_by e.g. ['country_name'].
    filters e.g. [{"field":"content_type","op":"eq","value":"transactional"}]."""
    return _run_report(metric, agg, group_by, filters, granularity, limit)


@mcp.tool()
def list_escalations() -> dict:
    """List the open route escalations the ambient monitoring agent has raised,
    each with route, rule, severity, evidence, and the RECOMMENDED action.
    Acting on them (block/dismiss) is a human task in the portal, not a tool."""
    return _list_escalations()


@mcp.tool()
def get_route_detail(client_id: str | None = None, country_name: str | None = None,
                     operator_name: str | None = None, vendor_name: str | None = None,
                     minutes: int = 30) -> dict:
    """Recent traffic for one route (messages, delivery_rate, profit) over the
    last N minutes — context a human can review before deciding on an escalation."""
    return _get_route_detail(client_id, country_name, operator_name, vendor_name, minutes)


@mcp.tool()
def get_window_summary() -> dict:
    """Overall live-traffic health: sim clock, message volume, delivery rate,
    blocked-route count, escalation counts."""
    return _get_window_summary()


if __name__ == "__main__":
    mcp.run()
