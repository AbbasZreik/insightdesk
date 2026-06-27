"""
Lightweight observability for InsightDesk (Day 5 concept).

Every turn emits a structured trace event — the question, the generated spec,
which backend tools were called, row counts, anomalies, and latency — as one
JSON line. That gives you a real audit trail for debugging and for showing
judges the agent's decisions, without pulling in a heavy telemetry stack.

Usage:
    tracer = JsonlTracer("traces.jsonl")
    agent = InsightAgent(backend, llm, tracer=tracer)
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class TraceEvent:
    question: str
    spec: dict[str, Any] | None = None
    tool_calls: list[str] = field(default_factory=list)
    row_count: int = 0
    anomaly_count: int = 0
    repaired: bool = False
    error: str | None = None
    latency_ms: float = 0.0
    ts: float = field(default_factory=time.time)


class Tracer(Protocol):
    def emit(self, event: TraceEvent) -> None: ...


class NullTracer:
    def emit(self, event: TraceEvent) -> None:  # no-op
        pass


class JsonlTracer:
    """Append one JSON line per turn. Pass path=None to write to stderr."""

    def __init__(self, path: str | None = "traces.jsonl"):
        self.path = path

    def emit(self, event: TraceEvent) -> None:
        line = json.dumps(asdict(event), default=str)
        if self.path:
            with open(self.path, "a") as f:
                f.write(line + "\n")
        else:
            print(line, file=sys.stderr)
