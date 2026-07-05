"""
InsightDesk CLI — run the agent against your OWN Gemini API key.

Setup:
    pip install -r requirements.txt
    export GEMINI_API_KEY=...        # from Google AI Studio (NOT Antigravity)
    python -m insightdesk.data.seed  # if you haven't generated data yet

Usage:
    python -m insightdesk.run_agent "What is the monthly cost trend for Europe?"
    python -m insightdesk.run_agent            # interactive REPL (keeps memory)
    python -m insightdesk.run_agent --debug "Top clients by cost"

This path uses your personal Gemini API quota and is completely independent of
Antigravity's built-in agent quota.
"""
from __future__ import annotations

import argparse
import sys

from .agent.llm import DEFAULT_MODEL, GeminiLLM
from .agent.orchestrator import InsightAgent
from .agent.spec_agent import SpecError
from .backends.cdr_backend import CDRBackend

DB = "insightdesk/data/cdr.duckdb"


def _print_turn(turn, debug: bool) -> None:
    print(f"\n{turn.answer}")
    if turn.anomalies:
        print("  anomalies:")
        for a in turn.anomalies:
            print(f"    - {a}")
    if debug:
        print("  spec:", turn.spec.to_dict())
        print(f"  ({len(turn.rows)} rows)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Ask the InsightDesk agent a question.")
    ap.add_argument("question", nargs="*", help="question to ask (omit for REPL)")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--debug", action="store_true", help="show spec + row count")
    ap.add_argument("--skill", default=None,
                    help="anomaly skill: a built-in name (auto, sharp_drop, "
                         "sharp_spike, underperformer) or a plain-English rule "
                         "to compile, e.g. \"flag regions below 80% of the top\"")
    args = ap.parse_args()

    try:
        llm = GeminiLLM(model=args.model)
    except ImportError:
        print("error: the google-genai SDK isn't installed.", file=sys.stderr)
        print("Run: pip install google-genai", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        print("Set GEMINI_API_KEY from Google AI Studio and try again.",
              file=sys.stderr)
        sys.exit(1)

    agent = InsightAgent(backend=CDRBackend(args.db), llm=llm)

    if args.skill:
        from .agent.skills import resolve_skill
        try:
            agent.anomaly_skill = resolve_skill(args.skill, llm, agent.backend.get_schema())
            print(f"[anomaly skill: {agent.anomaly_skill.name} — "
                  f"{agent.anomaly_skill.description}]")
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)

    def handle(q: str) -> None:
        try:
            _print_turn(agent.ask(q), args.debug)
        except SpecError as e:
            print(f"\nI can't answer that from this dataset: {e}")

    if args.question:
        handle(" ".join(args.question))
        return

    print(f"InsightDesk ({args.model}). Ask a question, or 'exit'. "
          "Follow-ups remember context.")
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in {"exit", "quit", "q"}:
            break
        if q:
            handle(q)


if __name__ == "__main__":
    main()
