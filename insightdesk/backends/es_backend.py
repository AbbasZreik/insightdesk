"""
Elasticsearch backend — the *production showcase* adapter.

Same AggregationSpec contract as DuckDB; the agent cannot tell which one it is
talking to. This is the adapter that demonstrates your real Mada-style skill
(ES aggregation queries), while the DuckDB adapter keeps the public live demo
cheap and always-on.

Design notes for the writeup:
  - plan / status / client_name are denormalised onto each event document at
    index time (see bulk_load) so they are groupable without a join — the
    idiomatic ES pattern.
  - Multi-dimension group_by + time bucketing is handled with a `composite`
    aggregation, which paginates cleanly and avoids the cardinality pitfalls
    of deeply nested `terms` aggregations.

NOTE: untested against a live cluster in this skeleton — run the smoke test in
__main__ against a real ES 8.x before relying on it.
"""
from __future__ import annotations

from typing import Any

from .base import (
    AggregationBackend,
    AggregationSpec,
    Filter,
    SchemaInfo,
    validate_spec,
)

_METRICS: dict[str, str] = {"cost": "sum", "message_count": "sum"}
_DIMENSIONS = ["client_id", "region", "product", "plan", "status", "client_name"]
_CAL_INTERVAL = {"day": "1d", "week": "1w", "month": "1M"}

INDEX_MAPPING: dict[str, Any] = {
    "mappings": {
        "properties": {
            "event_date": {"type": "date"},
            "client_id": {"type": "keyword"},
            "client_name": {"type": "keyword"},
            "region": {"type": "keyword"},
            "product": {"type": "keyword"},
            "plan": {"type": "keyword"},
            "status": {"type": "keyword"},
            "message_count": {"type": "long"},
            "cost": {"type": "double"},
        }
    }
}


class ESBackend(AggregationBackend):
    def __init__(self, hosts: str | list[str], index: str = "messaging_events",
                 **client_kwargs: Any):
        from elasticsearch import Elasticsearch
        self.es = Elasticsearch(hosts, **client_kwargs)
        self.index = index

    # -- schema ------------------------------------------------------------
    def get_schema(self) -> SchemaInfo:
        dim_values: dict[str, list[str]] = {}
        for d in ("region", "product", "plan", "status"):
            resp = self.es.search(
                index=self.index, size=0,
                aggs={"vals": {"terms": {"field": d, "size": 100}}},
            )
            dim_values[d] = [b["key"] for b in resp["aggregations"]["vals"]["buckets"]]
        return SchemaInfo(
            table=self.index,
            time_field="event_date",
            metrics=_METRICS,                      # type: ignore[arg-type]
            dimensions=_DIMENSIONS,
            dimension_values=dim_values,
        )

    # -- query -------------------------------------------------------------
    def run_aggregation(self, spec: AggregationSpec) -> list[dict[str, Any]]:
        schema = self.get_schema()
        validate_spec(spec, schema)            # same guardrail as DuckDB
        body = self._build_query(spec)
        resp = self.es.search(index=self.index, **body)

        # No group_by -> single global metric value.
        if not spec.group_by:
            val = resp["aggregations"]["value"]["value"] if spec.agg != "count" \
                else resp["hits"]["total"]["value"]
            return [{"value": val}]

        rows: list[dict[str, Any]] = []
        for bucket in resp["aggregations"]["grouped"]["buckets"]:
            row = dict(bucket["key"])  # composite key -> {source_name: value}
            if "__time__" in row:      # rename time source to "bucket"
                row["bucket"] = row.pop("__time__")
            row["value"] = (bucket["doc_count"] if spec.agg == "count"
                            else bucket["value"]["value"])
            rows.append(row)

        # Composite sorts by key; emulate order_by=value / limit client-side.
        order = spec.order_by or "value"
        if order in (rows[0] if rows else {}):
            rows.sort(key=lambda r: r[order], reverse=spec.order_desc)
        if spec.limit:
            rows = rows[: spec.limit]
        return rows

    # -- spec -> ES DSL ----------------------------------------------------
    def _build_query(self, spec: AggregationSpec) -> dict[str, Any]:
        body: dict[str, Any] = {"size": 0, "query": self._build_filter(spec)}

        metric_agg = (
            {"value_count": {"field": "event_date"}} if spec.agg == "count"
            else {spec.agg: {"field": spec.metric}}
        )

        if not spec.group_by:
            body["aggs"] = {"value": metric_agg}
            return body

        sources: list[dict[str, Any]] = []
        for g in spec.group_by:
            if g == "__time__":
                sources.append({"__time__": {"date_histogram": {
                    "field": "event_date",
                    "calendar_interval": _CAL_INTERVAL[spec.granularity or "month"],
                    "format": "yyyy-MM-dd",
                }}})
            else:
                sources.append({g: {"terms": {"field": g}}})

        body["aggs"] = {
            "grouped": {
                "composite": {"size": 1000, "sources": sources},
                "aggs": {} if spec.agg == "count" else {"value": metric_agg},
            }
        }
        return body

    def _build_filter(self, spec: AggregationSpec) -> dict[str, Any]:
        must: list[dict[str, Any]] = []
        rng: dict[str, Any] = {}
        if spec.time_start:
            rng["gte"] = spec.time_start
        if spec.time_end:
            rng["lte"] = spec.time_end
        if rng:
            must.append({"range": {"event_date": rng}})
        for f in spec.filters:
            must.append(_filter_dsl(f))
        return {"bool": {"filter": must}} if must else {"match_all": {}}

    # -- indexing helper ---------------------------------------------------
    def bulk_load(self, events_parquet: str, clients_parquet: str) -> int:
        """Create the index and load events with client dims denormalised in."""
        import pandas as pd
        from elasticsearch.helpers import bulk

        events = pd.read_parquet(events_parquet)
        clients = pd.read_parquet(clients_parquet)[
            ["client_id", "client_name", "plan", "status"]
        ]
        df = events.merge(clients, on="client_id", how="left")

        if self.es.indices.exists(index=self.index):
            self.es.indices.delete(index=self.index)
        self.es.indices.create(index=self.index, **INDEX_MAPPING)

        actions = ({"_index": self.index, "_source": rec}
                   for rec in df.to_dict(orient="records"))
        ok, _ = bulk(self.es, actions)
        self.es.indices.refresh(index=self.index)
        return ok


def _filter_dsl(f: Filter) -> dict[str, Any]:
    if f.op == "eq":
        return {"term": {f.field: f.value}}
    if f.op == "in":
        return {"terms": {f.field: list(f.value)}}
    if f.op in ("gte", "lte"):
        return {"range": {f.field: {f.op: f.value}}}
    raise ValueError(f"Unsupported filter op {f.op!r}")


if __name__ == "__main__":
    # Smoke test against a local ES 8.x — adjust hosts/auth as needed.
    be = ESBackend("http://localhost:9200")
    n = be.bulk_load("insightdesk/data/events.parquet",
                     "insightdesk/data/clients.parquet")
    print(f"indexed {n} docs")
    print(be.run_aggregation(
        AggregationSpec(metric="cost", agg="sum", group_by=["region"])
    ))
