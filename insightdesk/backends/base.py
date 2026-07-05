"""
Backend-agnostic query contract for InsightDesk.

The agent NEVER writes raw SQL or Elasticsearch DSL. It only ever produces an
`AggregationSpec` (validated against the live schema). Each backend translates
that same spec into its own native query. This is what makes the data store
swappable (DuckDB for the public demo, Elasticsearch for the production
showcase) and is also the core guardrail: anything outside the declared schema
is rejected before a query ever runs.

Course-concept hooks (call these out by name in the writeup):
  - Tools / MCP (Day 2): get_schema() + run_aggregation() are the two tools.
  - Quality & guardrails (Day 4): validate_spec() rejects out-of-scope queries.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Query contract
# ---------------------------------------------------------------------------

AggFunc = Literal["sum", "avg", "count", "min", "max"]
Granularity = Literal["minute", "hour", "day", "week", "month"]


@dataclass
class Filter:
    """A single equality / range filter on a dimension or the time field."""
    field: str
    op: Literal["eq", "in", "gte", "lte"]
    value: Any


@dataclass
class AggregationSpec:
    """A backend-agnostic description of an aggregation query.

    Example — "total cost per region for SMS in the last 90 days":
        AggregationSpec(
            metric="cost", agg="sum", group_by=["region"],
            filters=[Filter("product", "eq", "SMS")],
            time_field="event_date", granularity=None,
        )
    """
    metric: str                       # which measure to aggregate, e.g. "cost"
    agg: AggFunc = "sum"
    group_by: list[str] = field(default_factory=list)   # dimensions, e.g. ["region"]
    filters: list[Filter] = field(default_factory=list)
    time_field: str | None = None     # set when grouping/filtering by time
    granularity: Granularity | None = None              # time bucket size
    time_start: str | None = None     # ISO date "YYYY-MM-DD"
    time_end: str | None = None
    order_by: str | None = None       # "value" (the metric) or a group_by field
    order_desc: bool = True
    limit: int | None = 50

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric, "agg": self.agg, "group_by": self.group_by,
            "filters": [vars(f) for f in self.filters],
            "time_field": self.time_field, "granularity": self.granularity,
            "time_start": self.time_start, "time_end": self.time_end,
            "order_by": self.order_by, "order_desc": self.order_desc,
            "limit": self.limit,
        }


@dataclass
class SchemaInfo:
    """What the agent is allowed to touch. Returned by get_schema() so the
    model can ground its specs and so validate_spec() has something to check
    against."""
    table: str
    time_field: str
    metrics: dict[str, AggFunc]          # metric name -> default/typical agg
    dimensions: list[str]                # groupable / filterable fields
    dimension_values: dict[str, list[str]]  # small-cardinality value domains


# ---------------------------------------------------------------------------
# Guardrail: validate a spec against the schema BEFORE running it
# ---------------------------------------------------------------------------

_VALID_AGGS = {"sum", "avg", "count", "min", "max"}
_VALID_GRANULARITY = {None, "minute", "hour", "day", "week", "month"}
_MAX_LIMIT = 1000


def validate_spec(spec: AggregationSpec, schema: SchemaInfo) -> None:
    """Raise ValueError if the spec references anything outside the schema.

    This is the single choke point that keeps a hallucinated field, an unknown
    metric, or an absurd limit from ever reaching the data store.
    """
    if spec.metric not in schema.metrics and not (
        spec.agg == "count" and spec.metric in {"*", "events", "event"}
    ):
        raise ValueError(
            f"Unknown metric {spec.metric!r}. Allowed: {sorted(schema.metrics)}"
        )
    if spec.agg not in _VALID_AGGS:
        raise ValueError(f"Unknown agg {spec.agg!r}. Allowed: {sorted(_VALID_AGGS)}")
    if spec.granularity not in _VALID_GRANULARITY:
        raise ValueError(f"Unknown granularity {spec.granularity!r}.")

    allowed_dims = set(schema.dimensions)
    for g in spec.group_by:
        if g == "__time__":
            continue  # special token: bucket by the time field at `granularity`
        if g not in allowed_dims:
            raise ValueError(
                f"Cannot group by {g!r}. Allowed dimensions: {sorted(allowed_dims)}"
            )

    for f in spec.filters:
        if f.field != schema.time_field and f.field not in allowed_dims:
            raise ValueError(
                f"Cannot filter on {f.field!r}. "
                f"Allowed: {sorted(allowed_dims | {schema.time_field})}"
            )
        if f.op not in {"eq", "in", "gte", "lte"}:
            raise ValueError(f"Unknown filter op {f.op!r}.")
        # Scope-check categorical values when we know the domain.
        if f.field in schema.dimension_values and f.op in {"eq", "in"}:
            domain = set(schema.dimension_values[f.field])
            vals = f.value if isinstance(f.value, (list, tuple, set)) else [f.value]
            unknown = [v for v in vals if v not in domain]
            if unknown:
                raise ValueError(
                    f"Unknown value(s) {unknown} for {f.field!r}. "
                    f"Known values: {sorted(domain)}"
                )

    if spec.limit is not None and not (1 <= spec.limit <= _MAX_LIMIT):
        raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}.")


# ---------------------------------------------------------------------------
# The interface every backend implements
# ---------------------------------------------------------------------------

class AggregationBackend(ABC):
    """Implemented by DuckDBBackend (demo) and ESBackend (production showcase).

    The two methods below are exactly what gets exposed as MCP tools to the
    agent. Keeping the surface this small is deliberate: the agent's whole job
    is to produce a valid AggregationSpec, not to author queries.
    """

    @abstractmethod
    def get_schema(self) -> SchemaInfo:
        """Return the queryable schema so the agent can ground its specs."""

    @abstractmethod
    def run_aggregation(self, spec: AggregationSpec) -> list[dict[str, Any]]:
        """Validate, translate, execute, and return rows as a list of dicts.

        Each row has the group_by fields plus a single "value" key holding the
        aggregated metric (and "bucket" when grouping by time)."""

    def close(self) -> None:  # optional override
        pass
