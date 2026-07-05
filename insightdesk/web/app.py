"""
InsightDesk (SMS traffic edition) — web app.

Two surfaces over one CDR dataset:
  - Reporting agent: natural-language questions -> validated query -> answer+chart.
  - Monitoring portal: an ambient agent that ingests CDR batches, raises route
    escalations, and lets a human block or dismiss them.

The monitoring agent can run as a real server-side background loop: once started
it advances the traffic stream and re-evaluates the rules on a timer, so
escalations accumulate even with no browser open. Start it from the portal's
Auto button, via POST /monitor/start, or at boot with INSIGHTDESK_AUTORUN=1.

Run:
    export GEMINI_API_KEY=...
    uvicorn insightdesk.web.app:app --reload     # http://localhost:8000
Offline UI test:
    INSIGHTDESK_MOCK=1 uvicorn insightdesk.web.app:app
"""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..agent.llm import LLM, MockLLM
from ..agent.orchestrator import InsightAgent, Turn
from ..agent.spec_agent import SpecError
from ..agent.trace import JsonlTracer
from ..backends.cdr_backend import CDRBackend
from ..monitor.engine import MonitoringAgent
from ..monitor.stream import CDRStream

DB = os.environ.get("INSIGHTDESK_DB", "insightdesk/data/cdr.duckdb")
PARQUET = os.environ.get("INSIGHTDESK_PARQUET", "insightdesk/data/cdr.parquet")
BATCH_MIN = int(os.environ.get("INSIGHTDESK_BATCH_MIN", "5"))
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="InsightDesk — SMS Traffic")

_backend = CDRBackend(DB)
_tracer = JsonlTracer(os.environ.get("INSIGHTDESK_TRACE", "/tmp/traces.jsonl"))
_agents: dict[str, InsightAgent] = {}

# Shared monitoring state. The lock guards stream-advance + evaluate so the
# manual /feed endpoint and the background loop never race.
_monitor = MonitoringAgent()
_stream = CDRStream(PARQUET, batch_min=BATCH_MIN)
_lock = threading.Lock()
_bg = {"running": False, "done": False, "task": None,
       "interval": float(os.environ.get("INSIGHTDESK_AUTORUN_INTERVAL", "2.0"))}


# -- reporting --------------------------------------------------------------

def _make_llm() -> LLM:
    if os.environ.get("INSIGHTDESK_MOCK") == "1":
        return _demo_mock()
    from ..agent.llm import GeminiLLM
    return GeminiLLM()


def _agent_for(session_id: str) -> InsightAgent:
    if session_id not in _agents:
        _agents[session_id] = InsightAgent(backend=_backend, llm=_make_llm(), tracer=_tracer)
    return _agents[session_id]


def _chart(turn: Turn) -> dict | None:
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
    return {"type": "line" if is_time else "bar", "labels": labels, "values": values,
            "anomaly_indices": [i for i, l in enumerate(labels) if l in anomaly_labels],
            "metric": turn.spec.metric}


class AskRequest(BaseModel):
    question: str
    session_id: str = "default"
    anomaly_skill: str | None = None


# Optional ADK reporting path (set INSIGHTDESK_ADK=1). Falls back to the
# hand-built agent otherwise, so this is safe to ship either way.
_adk_service = None


def _adk():
    global _adk_service
    if _adk_service is None:
        from ..adk.reporting_agent import build_reporting_agent
        from ..adk.service import ADKService
        _adk_service = ADKService(build_reporting_agent())
    return _adk_service


def _chart_from_report(report: dict) -> dict | None:
    rows = report.get("rows") or []
    spec = report.get("spec") or {}
    if not rows or "value" not in rows[0]:
        return None
    is_time = "bucket" in rows[0]
    gb = spec.get("group_by") or []
    label_key = "bucket" if is_time else (gb[0] if gb else None)
    if label_key is None:
        return None
    labels = [str(r.get(label_key)) for r in rows]
    anomaly_labels = {str(a["label"]) for a in report.get("anomalies", [])}
    return {"type": "line" if is_time else "bar", "labels": labels,
            "values": [r["value"] for r in rows],
            "anomaly_indices": [i for i, l in enumerate(labels) if l in anomaly_labels],
            "metric": spec.get("metric")}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "mock": os.environ.get("INSIGHTDESK_MOCK") == "1",
            "adk": os.environ.get("INSIGHTDESK_ADK") == "1"}


@app.post("/ask")
def ask(req: AskRequest) -> dict:
    if os.environ.get("INSIGHTDESK_ADK") == "1":
        import asyncio
        try:
            out = asyncio.run(_adk().ask(req.question, session_id=req.session_id))
        except Exception as e:
            return {"ok": False, "answer": f"ADK agent error: {e}"}
        report = out.get("report") or {}
        return {"ok": True, "answer": out.get("answer", ""),
                "spec": report.get("spec"), "rows": (report.get("rows") or [])[:50],
                "anomalies": report.get("anomalies", []),
                "chart": _chart_from_report(report) if report else None}
    agent = _agent_for(req.session_id)
    if req.anomaly_skill:
        try:
            from ..agent.skills import resolve_skill
            agent.anomaly_skill = resolve_skill(req.anomaly_skill, agent.llm, _backend.get_schema())
        except ValueError as e:
            return {"ok": False, "answer": f"Couldn't apply that anomaly rule: {e}"}
    else:
        agent.anomaly_skill = None
    try:
        turn = agent.ask(req.question)
    except SpecError as e:
        return {"ok": False, "answer": f"I can't answer that from this dataset: {e}"}
    return {"ok": True, "answer": turn.answer, "spec": turn.spec.to_dict(),
            "rows": turn.rows[:50], "anomalies": turn.anomalies, "chart": _chart(turn)}


# -- monitoring portal ------------------------------------------------------

class ActionRequest(BaseModel):
    escalation_id: str
    action: str


def _advance_once() -> int | None:
    """Advance the stream by one batch and evaluate. Returns batch size, or
    None when the stream is exhausted. Thread-safe."""
    with _lock:
        if not _stream.has_next():
            return None
        batch = _stream.next_batch()
        if not batch.empty:
            _monitor.ingest(batch)
            _monitor.evaluate()
        return int(len(batch))


def _snapshot() -> dict:
    with _lock:
        return {"summary": _monitor.window_summary(),
                "open": [e.to_dict() for e in _monitor.store.open()],
                "counts": _monitor.store.counts()}


def _status() -> dict:
    snap = _snapshot()
    return {"running": _bg["running"], "interval": _bg["interval"],
            "done": _bg["done"], "has_next": _stream.has_next(), **snap}


async def _bg_loop() -> None:
    while _bg["running"]:
        size = await asyncio.to_thread(_advance_once)
        if size is None:
            _bg["running"] = False
            _bg["done"] = True
            break
        await asyncio.sleep(_bg["interval"])


@app.on_event("startup")
async def _maybe_autostart() -> None:
    if os.environ.get("INSIGHTDESK_AUTORUN") == "1":
        _bg["running"] = True
        _bg["done"] = False
        _bg["task"] = asyncio.create_task(_bg_loop())


@app.post("/monitor/start")
async def monitor_start(interval: float | None = None) -> dict:
    if interval:
        _bg["interval"] = max(0.2, float(interval))
    if not _bg["running"] and _stream.has_next():
        _bg["running"] = True
        _bg["done"] = False
        _bg["task"] = asyncio.create_task(_bg_loop())
    return _status()


@app.post("/monitor/stop")
async def monitor_stop() -> dict:
    _bg["running"] = False
    return _status()


@app.get("/monitor/status")
def monitor_status() -> dict:
    return _status()


@app.post("/feed")
def feed() -> dict:
    """Manually advance one batch (works whether or not the bg loop is running)."""
    size = _advance_once()
    snap = _snapshot()
    return {"done": size is None, "batch_size": size or 0, **snap}


@app.get("/escalations")
def escalations() -> dict:
    return _snapshot()


@app.post("/action")
def action(req: ActionRequest) -> dict:
    with _lock:
        if req.action == "block":
            esc = _monitor.store.block(req.escalation_id)
        elif req.action == "dismiss":
            esc = _monitor.store.dismiss(req.escalation_id)
        else:
            return {"ok": False, "error": "action must be block or dismiss"}
        counts = _monitor.store.counts()
    return {"ok": esc is not None, "escalation": esc.to_dict() if esc else None,
            "counts": counts}


@app.post("/reset")
async def reset() -> dict:
    global _monitor, _stream
    _bg["running"] = False
    await asyncio.sleep(0)
    with _lock:
        _monitor = MonitoringAgent()
        _stream = CDRStream(PARQUET, batch_min=BATCH_MIN)
        _bg["done"] = False
    return {"ok": True}


def _demo_mock() -> MockLLM:
    def json_fn(system: str, user: str) -> dict:
        u = user.lower().split("new question:")[-1]
        if "delivery rate" in u and "country" in u:
            return {"metric": "delivered", "agg": "avg", "group_by": ["country_name"]}
        if "profit" in u and "vendor" in u:
            return {"metric": "profit", "agg": "sum", "group_by": ["vendor_name"]}
        if "messages" in u or "volume" in u or "traffic" in u:
            return {"metric": "message_count", "agg": "count", "group_by": ["country_name"]}
        return {"error": "mock handles: delivery rate by country, profit by vendor, volume by country"}

    def text_fn(system: str, user: str) -> str:
        import json
        p = json.loads(user)
        return f"Results across {len(p['results'])} groups." + (
            f" Anomaly: {p['anomalies'][0]['label']}." if p.get("anomalies") else "")
    return MockLLM(json_responses=json_fn, text_responses=text_fn)
