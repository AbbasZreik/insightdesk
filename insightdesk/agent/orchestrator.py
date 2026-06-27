"""
Orchestration: question in, grounded answer out.

InsightAgent ties the pieces together and adds lightweight SESSION MEMORY (the
Day 3 concept): it keeps the last few (question -> spec) turns and feeds a
compact summary into the spec prompt, so a follow-up like "now break that down
by region" resolves against the previous question instead of starting cold.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..backends.base import AggregationBackend, AggregationSpec
from .anomaly import detect_anomalies
from .llm import LLM
from .prompts import build_narrator_system, build_narrator_user
from .spec_agent import SpecError, text_to_spec
from .trace import NullTracer, TraceEvent, Tracer


@dataclass
class Turn:
    question: str
    spec: AggregationSpec
    rows: list[dict]
    anomalies: list[dict]
    answer: str


@dataclass
class InsightAgent:
    backend: AggregationBackend
    llm: LLM
    history: list[Turn] = field(default_factory=list)
    memory_turns: int = 3            # how many prior turns to carry as context
    tracer: Tracer = field(default_factory=NullTracer)

    def _question_with_memory(self, question: str) -> str:
        if not self.history:
            return question
        recent = self.history[-self.memory_turns:]
        lines = [
            f"- previously asked: {t.question!r} -> spec {t.spec.to_dict()}"
            for t in recent
        ]
        return (
            "Conversation so far (resolve references like 'that' / 'those' "
            "against it):\n" + "\n".join(lines) + f"\n\nNew question: {question}"
        )

    def narrate(self, question: str, rows: list[dict], anomalies: list[dict]) -> str:
        return self.llm.generate_text(
            build_narrator_system(),
            build_narrator_user(question, rows, anomalies),
        )

    def ask(self, question: str) -> Turn:
        start = time.time()
        event = TraceEvent(question=question)
        try:
            schema = self.backend.get_schema()
            event.tool_calls.append("get_schema")
            result = text_to_spec(self.llm, schema, self._question_with_memory(question))
            spec = result.spec
            event.spec = spec.to_dict()
            event.repaired = result.repaired

            rows = self.backend.run_aggregation(spec)
            event.tool_calls.append("run_aggregation")
            event.row_count = len(rows)

            anomalies = detect_anomalies(rows, spec.group_by)
            event.anomaly_count = len(anomalies)

            answer = self.narrate(question, rows, anomalies)
            turn = Turn(question, spec, rows, anomalies, answer)
            self.history.append(turn)
            return turn
        except SpecError as e:
            event.error = str(e)
            raise
        finally:
            event.latency_ms = round((time.time() - start) * 1000, 1)
            self.tracer.emit(event)

    def reset(self) -> None:
        self.history.clear()
