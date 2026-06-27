"""
End-to-end pipeline test with a mock model (no API key / network / quota).

Proves: NL -> spec -> validate -> DuckDB -> anomaly detect -> narrate, plus the
self-repair retry and session-memory follow-up. Run:

    python -m insightdesk.tests.test_pipeline
"""
from __future__ import annotations

import json

from ..agent.llm import MockLLM
from ..agent.orchestrator import InsightAgent
from ..backends.duckdb_backend import DuckDBBackend

DB = "insightdesk/data/insightdesk.duckdb"


def make_mock() -> MockLLM:
    """Canned spec per question keyword, plus a repair example."""
    def json_fn(system: str, user: str) -> dict:
        u_full = user.lower()
        # --- self-repair demo: first answer is bad, repair returns good spec ---
        if "validation error" in u_full:                 # this is a repair call
            return {"metric": "cost", "agg": "sum", "group_by": ["region"]}
        # Match only the NEW question, not the carried memory context.
        u = u_full.split("new question:")[-1]
        if "cost by region" in u or "cost per region" in u:
            return {"metric": "cost", "agg": "sum", "group_by": ["region"]}
        # --- memory follow-up: "now just for SMS" reuses prior region grouping --
        if "now just for sms" in u or "just for sms" in u:
            return {"metric": "cost", "agg": "sum", "group_by": ["region"],
                    "filters": [{"field": "product", "op": "eq", "value": "SMS"}]}
        # --- time-series: Europe monthly trend (exposes the revenue dip) --------
        if "europe" in u and ("month" in u or "trend" in u):
            return {"metric": "cost", "agg": "sum", "group_by": ["__time__"],
                    "granularity": "month",
                    "filters": [{"field": "region", "op": "eq", "value": "Europe"}],
                    "order_by": "bucket", "order_desc": False}
        # --- time-series: Falcon Media daily SMS (exposes the usage spike) ------
        if "falcon" in u and "sms" in u:
            return {"metric": "message_count", "agg": "sum", "group_by": ["__time__"],
                    "granularity": "day",
                    "filters": [{"field": "client_id", "op": "eq", "value": "C001"},
                                {"field": "product", "op": "eq", "value": "SMS"}],
                    "time_start": "2025-03-29", "time_end": "2025-04-12",
                    "order_by": "bucket", "order_desc": False}
        # --- guardrail demo: hallucinated field, forces a repair ---------------
        if "by country" in u and "validation error" not in u:
            return {"metric": "cost", "agg": "sum", "group_by": ["country"]}
        return {"error": "unsupported question in mock"}

    def text_fn(system: str, user: str) -> str:
        payload = json.loads(user)
        n_anom = len(payload.get("anomalies", []))
        return f"[mock answer over {len(payload['results'])} rows, {n_anom} anomalies]"

    return MockLLM(json_responses=json_fn, text_responses=text_fn)


def main() -> None:
    agent = InsightAgent(backend=DuckDBBackend(DB), llm=make_mock())

    print("== 1. cost by region (categorical) ==")
    t = agent.ask("Show me cost by region")
    print("spec:", t.spec.to_dict()["group_by"], t.spec.to_dict()["metric"])
    print("rows:", [(r["region"], round(r["value"], 2)) for r in t.rows])
    print("answer:", t.answer)

    print("\n== 2. memory follow-up: 'now just for SMS' ==")
    t = agent.ask("Now just for SMS")
    has_sms = any(f.value == "SMS" for f in t.spec.filters)
    print("carried region grouping + added SMS filter:", t.spec.group_by, "SMS?", has_sms)
    print("rows:", [(r["region"], round(r["value"], 2)) for r in t.rows])

    print("\n== 3. Europe monthly trend (should flag the revenue dip) ==")
    t = agent.ask("What is the monthly cost trend for Europe?")
    print("series:", [(str(r["bucket"])[:7], round(r["value"], 0)) for r in t.rows])
    print("anomalies:", t.anomalies)
    assert any(a["direction"] == "drop" for a in t.anomalies), "should detect the dip"

    print("\n== 4. Falcon Media daily SMS (should flag the usage spike) ==")
    t = agent.ask("Show Falcon Media daily SMS volume in early April")
    print("anomalies:", [(str(a["label"]), a["value"], a["direction"]) for a in t.anomalies])
    assert any(a["direction"] == "spike" for a in t.anomalies), "should detect the spike"

    print("\n== 5. guardrail + self-repair: 'group by country' ==")
    agent.reset()
    t = agent.ask("Show cost by country")  # bad field -> repair -> region
    print("recovered spec group_by:", t.spec.group_by)
    assert t.spec.group_by == ["region"], "repair should fix the bad field"

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
