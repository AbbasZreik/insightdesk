"""
Reporting agent on Google ADK (LlmAgent).

The ADK agent owns the plan -> call-tool -> narrate loop. The tools are the same
validated, deterministic functions the rest of the system uses, so detection and
arithmetic stay in code and the model never writes SQL or invents a number.

Auth (AI Studio key): set
    GOOGLE_GENAI_USE_VERTEXAI=FALSE
    GOOGLE_API_KEY=<your AI Studio key>
"""
from __future__ import annotations

import os
from typing import Any

from google.adk.agents import LlmAgent

from ..backends.base import AggregationSpec, Filter
from ..backends.cdr_backend import CDRBackend
from ..agent.anomaly import detect_anomalies

DB = os.environ.get("INSIGHTDESK_DB", "insightdesk/data/cdr.duckdb")
MODEL = os.environ.get("INSIGHTDESK_MODEL", "gemini-2.5-flash")

_backend: CDRBackend | None = None


def _be() -> CDRBackend:
    global _backend
    if _backend is None:
        _backend = CDRBackend(DB)
    return _backend


# -- tools (plain typed functions -> ADK tools) ----------------------------

def get_schema() -> dict:
    """Return the SMS CDR reporting schema: the metrics you may aggregate and the
    dimensions you may group by or filter on, plus their allowed values."""
    s = _be().get_schema()
    return {"metrics": s.metrics, "dimensions": s.dimensions,
            "dimension_values": s.dimension_values,
            "note": "delivery rate = metric 'delivered' agg 'avg'; "
                    "'message_count' counts rows; 'profit' is margin."}


def run_report(metric: str, agg: str = "sum", group_by: list[str] | None = None,
               country_name: str | None = None, operator_name: str | None = None,
               content_type: str | None = None, client_id: str | None = None,
               granularity: str | None = None, limit: int = 50) -> dict:
    """Run a read-only aggregation over the SMS CDR data and flag anomalies.

    metric: one of message_count, cost, profit, rate, delivered, latency_sec
            (delivery rate = 'delivered' with agg 'avg').
    agg: sum | avg | count | min | max.
    group_by: dimensions to break down by, e.g. ['country_name'] or ['__time__'].
    country_name/operator_name/content_type/client_id: optional equality filters.
    granularity: minute|hour|day for time grouping. Returns rows + anomalies.
    """
    filters = []
    for field, val in [("country_name", country_name), ("operator_name", operator_name),
                       ("content_type", content_type), ("client_id", client_id)]:
        if val:
            filters.append(Filter(field, "eq", val))
    gb = list(group_by or [])
    spec = AggregationSpec(metric=metric, agg=agg, group_by=gb, filters=filters,
                           granularity=granularity, limit=limit)
    rows = _be().run_aggregation(spec)            # validate_spec runs inside
    return {"row_count": len(rows), "rows": rows,
            "anomalies": detect_anomalies(rows, gb), "spec": spec.to_dict()}


INSTRUCTION = """\
You are InsightDesk, an SMS traffic reporting analyst. Answer questions about A2P
message traffic using ONLY the tools.

Always:
1. Call get_schema first if unsure which metric/dimension applies.
2. Call run_report to get the numbers. Never guess or invent figures.
3. Answer in 1-3 sentences using only values returned by run_report. If anomalies
   are flagged, mention the most significant one.
Delivery rate is the metric 'delivered' with agg 'avg' (0..1). Profit is margin.
If a question can't be answered from the schema, say so plainly."""


def build_reporting_agent() -> LlmAgent:
    return LlmAgent(
        model=MODEL,
        name="insightdesk_reporting",
        description="Answers SMS traffic questions over CDR data via read-only tools.",
        instruction=INSTRUCTION,
        tools=[get_schema, run_report],
    )
