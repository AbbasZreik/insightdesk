"""
Eval cases for the CDR reporting agent: question -> expected spec properties.
Partial match (assert only the listed fields) + guardrail cases that must be
rejected. Run: python -m insightdesk.eval.run_eval --mock
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class EvalCase:
    category: str
    question: str
    metric: str | None = None
    agg: str | None = None
    group_by: set[str] | None = None
    filters: set[tuple] | None = None
    must_error: bool = False
    notes: str = ""


CASES: list[EvalCase] = [
    EvalCase("aggregation", "Delivery rate by country",
             metric="delivered", agg="avg", group_by={"country_name"}),
    EvalCase("aggregation", "Profit by vendor",
             metric="profit", agg="sum", group_by={"vendor_name"}),
    EvalCase("aggregation", "Total messages overall",
             metric="message_count", group_by=set()),
    EvalCase("filter", "Delivery rate for transactional traffic",
             metric="delivered", agg="avg",
             filters={("content_type", "eq", "transactional")}),
    EvalCase("filter", "Messages to India",
             metric="message_count", filters={("country_name", "eq", "India")}),
    EvalCase("trend", "Hourly message volume",
             metric="message_count", group_by={"__time__"}),
    EvalCase("ranking", "Operators by delivery rate",
             metric="delivered", agg="avg", group_by={"operator_name"}),
    EvalCase("guardrail", "Cost broken down by ip address", must_error=True,
             notes="ip_address not a dimension"),
    EvalCase("guardrail", "Average revenue per salesperson", must_error=True,
             notes="revenue/salesperson not in schema"),
]
