"""
InsightDesk web app — the live, shareable demo (your capstone project link).

Serves a single-page chat UI and a /ask endpoint that runs the agent and returns
the answer, the spec, the rows, the anomalies, and chart-ready data. Per-session
memory lets follow-up questions resolve against earlier ones.

Run locally:
    export GEMINI_API_KEY=...        # your AI Studio key
    uvicorn insightdesk.web.app:app --reload
    # then open http://localhost:8000

Offline UI test (no key — canned answers for a few demo questions):
    INSIGHTDESK_MOCK=1 uvicorn insightdesk.web.app:app
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..agent.llm import LLM, MockLLM
from ..agent.orchestrator import InsightAgent, Turn
from ..agent.spec_agent import SpecError
from ..agent.trace import JsonlTracer
from ..backends.duckdb_backend import DuckDBBackend

DB = os.environ.get("INSIGHTDESK_DB", "insightdesk/data/insightdesk.duckdb")
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="InsightDesk")

# Shared, read-only backend + tracer; one agent per session for separate memory.
_backend = DuckDBBackend(DB)
_tracer = JsonlTracer(os.environ.get("INSIGHTDESK_TRACE", "traces.jsonl"))
_agents: dict[str, InsightAgent] = {}


def _make_llm() -> LLM:
    if os.environ.get("INSIGHTDESK_MOCK") == "1":
        return _demo_mock()
    from ..agent.llm import GeminiLLM
    return GeminiLLM()


def _agent_for(session_id: str) -> InsightAgent:
    if session_id not in _agents:
        _agents[session_id] = InsightAgent(
            backend=_backend, llm=_make_llm(), tracer=_tracer
        )
    return _agents[session_id]


def _chart(turn: Turn) -> dict | None:
    """Shape rows into Chart.js-ready data; mark anomalous points."""
    rows = turn.rows
    if not rows or "value" not in rows[0]:
        return None
    is_time = "bucket" in rows[0]
    label_key = "bucket" if is_time else (turn.spec.group_by[0] if turn.spec.group_by else None)
    if label_key is None:
        return None
    labels = [str(r.get(label_key)) for r in rows]
    values = [r["value"] for r in rows]
    anomaly_labels = {str(a["label"]) for a in turn.anomalies}
    anomaly_idx = [i for i, l in enumerate(labels) if l in anomaly_labels]
    return {
        "type": "line" if is_time else "bar",
        "labels": labels,
        "values": values,
        "anomaly_indices": anomaly_idx,
        "metric": turn.spec.metric,
    }


class AskRequest(BaseModel):
    question: str
    session_id: str = "default"
    anomaly_skill: str | None = None   # built-in name or a plain-English rule


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "mock": os.environ.get("INSIGHTDESK_MOCK") == "1"}


def _apply_skill(agent, source: str | None) -> str | None:
    """Set the agent's anomaly skill from a name/instruction; cache by source
    so a custom instruction is compiled at most once per session."""
    if not source:
        agent.anomaly_skill = None
        agent._skill_src = None
        return None
    if getattr(agent, "_skill_src", None) != source:
        from ..agent.skills import resolve_skill
        agent.anomaly_skill = resolve_skill(source, agent.llm, agent.backend.get_schema())
        agent._skill_src = source
    return agent.anomaly_skill.name


@app.post("/ask")
def ask(req: AskRequest) -> dict:
    agent = _agent_for(req.session_id)
    try:
        skill_name = _apply_skill(agent, req.anomaly_skill)
    except ValueError as e:
        return {"ok": False, "answer": f"Couldn't apply that anomaly rule: {e}"}
    try:
        turn = agent.ask(req.question)
    except SpecError as e:
        return {"ok": False, "answer": f"I can't answer that from this dataset: {e}"}
    return {
        "ok": True,
        "answer": turn.answer,
        "spec": turn.spec.to_dict(),
        "rows": turn.rows[:50],
        "anomalies": turn.anomalies,
        "chart": _chart(turn),
        "skill": skill_name,
    }


def _demo_mock() -> MockLLM:
    """Canned answers so the UI works offline without a key (limited set)."""
    def json_fn(system: str, user: str) -> dict:
        u = user.lower().split("new question:")[-1]
        if "region" in u and "cost" in u:
            return {"metric": "cost", "agg": "sum", "group_by": ["region"]}
        if "europe" in u:
            return {"metric": "cost", "agg": "sum", "group_by": ["__time__"],
                    "granularity": "month",
                    "filters": [{"field": "region", "op": "eq", "value": "Europe"}],
                    "order_by": "bucket", "order_desc": False}
        if "product" in u:
            return {"metric": "message_count", "agg": "sum", "group_by": ["product"]}
        return {"error": "mock only handles: cost by region, Europe trend, by product"}

    def text_fn(system: str, user: str) -> str:
        import json
        p = json.loads(user)
        a = p.get("anomalies", [])
        base = f"Here are the results across {len(p['results'])} groups."
        if a:
            x = a[0]
            base += (f" Note an anomaly: {x.get('label')} looks like a "
                     f"{x.get('direction')} versus the baseline.")
        return base
    return MockLLM(json_responses=json_fn, text_responses=text_fn)
