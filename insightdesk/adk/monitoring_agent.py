"""
Monitoring triage agent on ADK.

The deterministic rule engine (monitor/engine.py + rules.py) is unchanged and is
exposed as TOOLS. The ADK agent only triages and explains the escalations the
engine raises and recommends actions. It cannot block or dismiss — those remain
human actions in the portal. So detection stays deterministic and auditable; the
model adds natural-language triage on top.
"""
from __future__ import annotations

import os

from google.adk.agents import LlmAgent

from ..monitor.engine import MonitoringAgent
from ..monitor.stream import CDRStream

PARQUET = os.environ.get("INSIGHTDESK_PARQUET", "insightdesk/data/cdr.parquet")
MODEL = os.environ.get("INSIGHTDESK_MODEL", "gemini-2.5-flash")

_engine: MonitoringAgent | None = None
_stream: CDRStream | None = None


def _state():
    global _engine, _stream
    if _engine is None:
        _engine = MonitoringAgent()
        _stream = CDRStream(PARQUET, batch_min=5)
    return _engine, _stream


def evaluate_next_batch() -> dict:
    """Advance the live traffic stream by one batch, re-evaluate all monitoring
    rules, and return any escalations now open. Detection is deterministic."""
    engine, stream = _state()
    if not stream.has_next():
        return {"done": True, "open": [e.to_dict() for e in engine.store.open()]}
    batch = stream.next_batch()
    if not batch.empty:
        engine.ingest(batch)
        engine.evaluate()
    return {"done": not stream.has_next(), "batch_size": int(len(batch)),
            "summary": engine.window_summary(),
            "open": [e.to_dict() for e in engine.store.open()]}


def list_open_escalations() -> dict:
    """List the currently open route escalations with route, rule, severity,
    evidence, and the recommended action."""
    engine, _ = _state()
    return {"open": [e.to_dict() for e in engine.store.open()],
            "counts": engine.store.counts()}


def get_window_summary() -> dict:
    """Overall live-traffic health: clock, volume, delivery rate, counts."""
    engine, _ = _state()
    return engine.window_summary()


INSTRUCTION = """\
You are InsightDesk's traffic monitoring analyst. You watch SMS routes for trouble.

When asked to check traffic, call evaluate_next_batch (or list_open_escalations).
Then summarize the open escalations for a human operator: for each, state the
route, what is wrong (from the evidence), the severity, and the recommended action.
You DO NOT block or dismiss routes - you advise; a human acts in the portal.
Prioritize critical and high severity. Be concise and factual; use only the
evidence returned by the tools."""


def build_monitoring_agent() -> LlmAgent:
    return LlmAgent(
        model=MODEL,
        name="insightdesk_monitoring",
        description="Triages and explains SMS route escalations from the rule engine.",
        instruction=INSTRUCTION,
        tools=[evaluate_next_batch, list_open_escalations, get_window_summary],
    )
