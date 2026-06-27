"""
Eval cases for InsightDesk: question -> expected spec properties.

Each case asserts only the fields that matter for that question (partial match),
plus guardrail cases that must be REJECTED. This is the spec-accuracy eval set —
the thing that turns "it seemed to work in the demo" into a measurable number,
and that lets you catch regressions when you change the prompt or the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvalCase:
    category: str
    question: str
    # Expected partial spec (only assert what's listed):
    metric: str | None = None
    agg: str | None = None
    group_by: set[str] | None = None
    filters: set[tuple] | None = None         # {(field, op, value), ...}
    must_error: bool = False                  # guardrail: spec should be rejected
    notes: str = ""


CASES: list[EvalCase] = [
    # --- simple aggregation ---
    EvalCase("aggregation", "Total cost per region",
             metric="cost", agg="sum", group_by={"region"}),
    EvalCase("aggregation", "How many messages did each product send?",
             metric="message_count", agg="sum", group_by={"product"}),
    EvalCase("aggregation", "Total billed cost overall",
             metric="cost", agg="sum", group_by=set()),

    # --- filtering ---
    EvalCase("filter", "Total cost for SMS",
             metric="cost", agg="sum",
             filters={("product", "eq", "SMS")}),
    EvalCase("filter", "Cost per region for WhatsApp only",
             metric="cost", group_by={"region"},
             filters={("product", "eq", "WhatsApp")}),

    # --- time trends ---
    EvalCase("trend", "Monthly cost trend for Europe",
             metric="cost", group_by={"__time__"},
             filters={("region", "eq", "Europe")}),
    EvalCase("trend", "Daily message volume over time",
             metric="message_count", group_by={"__time__"}),

    # --- ranking ---
    EvalCase("ranking", "Top clients by cost",
             metric="cost", agg="sum", group_by={"client_id"}),

    # --- guardrail: must be rejected ---
    EvalCase("guardrail", "Cost broken down by country", must_error=True,
             notes="country is not a dimension"),
    EvalCase("guardrail", "Average revenue per salesperson", must_error=True,
             notes="revenue/salesperson not in schema"),
]
