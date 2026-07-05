"""
Prompt construction for InsightDesk.

Two jobs:
  1. build_spec_system(schema): teaches the model the live schema and the exact
     AggregationSpec JSON shape, and forbids anything off-schema. The model's
     ONLY job is to emit a spec — it never writes SQL or prose here.
  2. build_narrator_system(): constrains the model to answer strictly from the
     numbers it is given. This is the anti-fabrication guardrail for the
     natural-language answer (Day 4 concept, applied to output as well as input).
"""
from __future__ import annotations

import json

from ..backends.base import SchemaInfo

_SPEC_SHAPE = """\
Emit ONLY a JSON object with this shape (omit fields you don't need):
{
  "metric": "<one of the metrics below, or '*' with agg='count'>",
  "agg": "sum | avg | count | min | max",
  "group_by": ["<dimension>", "...", "__time__"],   // "__time__" buckets by time
  "granularity": "minute | hour | day | week | month",  // only with "__time__"
  "filters": [
    {"field": "<dimension or the time field>", "op": "eq|in|gte|lte", "value": <v>}
  ],
  "time_start": "YYYY-MM-DD",     // optional
  "time_end": "YYYY-MM-DD",       // optional
  "order_by": "value | <a group_by field>",
  "order_desc": true,
  "limit": 50
}
Rules:
- Use ONLY the metrics, dimensions, and dimension values listed below.
- Never invent a field, metric, or value. If the question can't be answered
  with this schema, return {"error": "<short reason>"}.
- To see a trend over time, put "__time__" in group_by and set "granularity".
- Return raw JSON only: no markdown, no backticks, no commentary."""


def build_spec_system(schema: SchemaInfo) -> str:
    dims_with_values = []
    for d in schema.dimensions:
        vals = schema.dimension_values.get(d)
        if vals:
            shown = ", ".join(map(str, vals[:12]))
            more = " ..." if len(vals) > 12 else ""
            dims_with_values.append(f"  - {d}: {shown}{more}")
        else:
            dims_with_values.append(f"  - {d}: (free-form / high cardinality)")

    return f"""\
You translate business questions into a single aggregation query spec over an \
SMS traffic dataset (CDR records: one row per message, with delivery status, \
cost, rate, and profit).

TIME FIELD: {schema.time_field} (a timestamp; you may bucket by minute/hour/day)
METRICS (numbers you may aggregate):
{chr(10).join(f"  - {m}" for m in schema.metrics)}
Note: delivery rate = metric "delivered" with agg "avg" (0..1). "message_count"
counts rows. "profit" is margin (rate minus cost).
DIMENSIONS (fields you may group by or filter on):
{chr(10).join(dims_with_values)}

{_SPEC_SHAPE}"""


def build_repair_user(question: str, bad_spec: dict, error: str) -> str:
    return (
        f"Your previous spec was invalid.\n"
        f"Question: {question}\n"
        f"Spec you returned: {json.dumps(bad_spec)}\n"
        f"Validation error: {error}\n"
        f"Return a corrected JSON spec that obeys the schema."
    )


def build_narrator_system() -> str:
    return """\
You are a business analyst writing a short, plain answer to a question, using \
ONLY the query results provided. Rules:
- Use only the numbers given. Never invent or estimate values not present.
- Be concise: 1-3 sentences. Lead with the direct answer.
- Round money to whole units and large counts sensibly.
- If anomalies are provided, call them out specifically (what, where, how big).
- If the results are empty, say no matching data was found."""


def build_narrator_user(question: str, rows: list[dict], anomalies: list[dict]) -> str:
    payload = {
        "question": question,
        "results": rows[:50],
        "anomalies": anomalies,
    }
    return json.dumps(payload, default=str)
