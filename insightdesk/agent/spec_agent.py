"""
Natural language -> validated AggregationSpec.

This is the heart of the "tools" concept: the model's output is structured, then
checked against the schema by validate_spec() BEFORE anything runs. If the model
produces an invalid spec, we feed the exact validation error back to it once and
let it self-correct — a cheap robustness win that also reads well to judges and
makes the eval set meaningful.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..backends.base import AggregationSpec, Filter, SchemaInfo, validate_spec
from .llm import LLM
from .prompts import build_repair_user, build_spec_system


class SpecError(ValueError):
    """The question could not be turned into a valid spec."""


@dataclass
class SpecResult:
    spec: AggregationSpec
    repaired: bool = False


def dict_to_spec(d: dict) -> AggregationSpec:
    """Build an AggregationSpec from a loosely-typed model dict."""
    if "error" in d:
        raise SpecError(str(d["error"]))
    filters = [
        Filter(field=f["field"], op=f.get("op", "eq"), value=f["value"])
        for f in d.get("filters", [])
    ]
    return AggregationSpec(
        metric=d.get("metric", "*"),
        agg=d.get("agg", "sum"),
        group_by=list(d.get("group_by", [])),
        filters=filters,
        time_field=d.get("time_field"),
        granularity=d.get("granularity"),
        time_start=d.get("time_start"),
        time_end=d.get("time_end"),
        order_by=d.get("order_by"),
        order_desc=d.get("order_desc", True),
        limit=d.get("limit", 50),
    )


def text_to_spec(llm: LLM, schema: SchemaInfo, question: str,
                 max_repairs: int = 1) -> SpecResult:
    system = build_spec_system(schema)
    raw = llm.generate_json(system, question)

    attempt = 0
    while True:
        try:
            spec = dict_to_spec(raw)
            validate_spec(spec, schema)
            return SpecResult(spec=spec, repaired=attempt > 0)
        except SpecError:
            raise  # model explicitly said it can't answer; don't loop
        except ValueError as err:
            if attempt >= max_repairs:
                raise SpecError(f"Could not build a valid spec: {err}") from err
            attempt += 1
            raw = llm.generate_json(system, build_repair_user(question, raw, str(err)))
