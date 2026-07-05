"""
LLM-as-judge for the reporting agent — grades the FINAL OUTPUT and the
TRAJECTORY.

Two metrics (see eval_config.yaml):
  - output_faithfulness: the narrated answer uses only numbers from the rows.
  - trajectory_validity:  the generated spec is on-schema and on-target, and
                          anomalies come from the deterministic skill.

The monitoring agent's reasoning is deterministic (skills) and is verified by
tests/test_monitor.py, so it is intentionally not judged here. This separation
is a quality property: consequential detection logic is not subject to model
whims, so a judge need not police it.

Run:
    python -m insightdesk.eval.judge --mock          # offline, deterministic
    GEMINI_API_KEY=... python -m insightdesk.eval.judge
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ..agent.llm import LLM, MockLLM
from ..agent.orchestrator import InsightAgent
from ..backends.cdr_backend import CDRBackend

CONFIG = Path(__file__).parent / "eval_config.yaml"
DB = os.environ.get("INSIGHTDESK_DB", "insightdesk/data/cdr.duckdb")

JUDGE_SYSTEM = """\
You are a strict evaluator of a data-reporting agent. You are given a user
QUESTION, the query SPEC the agent generated, the ROWS returned by running it,
any ANOMALIES flagged, and the agent's ANSWER.

Score two metrics, each strictly "pass" or "fail":
1. output_faithfulness: does the ANSWER use only numbers present in ROWS, with no
   invented or unsupported figures? Any fabricated number is a fail.
2. trajectory_validity: is the SPEC on-schema and does it actually answer the
   QUESTION (right metric, grouping, filters)? If it answers a different question
   or is malformed, fail.

Return ONLY this JSON, no prose:
{"output_faithfulness":"pass|fail","faithfulness_reason":"...",
 "trajectory_validity":"pass|fail","trajectory_reason":"..."}"""


def _load_config() -> dict:
    import yaml
    return yaml.safe_load(CONFIG.read_text())


def generate_traces(agent: InsightAgent, questions: list[str]) -> list[dict]:
    """Run the reporting agent and capture each trajectory."""
    traces = []
    for q in questions:
        try:
            turn = agent.ask(q)
            traces.append({"question": q, "spec": turn.spec.to_dict(),
                           "rows": turn.rows[:25], "anomalies": turn.anomalies,
                           "answer": turn.answer})
        except Exception as e:
            traces.append({"question": q, "error": str(e)})
    return traces


def judge_trace(judge: LLM, trace: dict) -> dict:
    if "error" in trace:
        return {"output_faithfulness": "fail", "faithfulness_reason": "agent errored",
                "trajectory_validity": "fail", "trajectory_reason": trace["error"]}
    user = json.dumps({"question": trace["question"], "spec": trace["spec"],
                       "rows": trace["rows"], "anomalies": trace["anomalies"],
                       "answer": trace["answer"]}, default=str)
    verdict = judge.generate_json(JUDGE_SYSTEM, user)
    for k in ("output_faithfulness", "trajectory_validity"):
        verdict.setdefault(k, "fail")
    return verdict


def _mock_agent_llm() -> MockLLM:
    def jf(system: str, user: str) -> dict:
        u = user.lower().split("new question:")[-1]
        if "delivery rate" in u and "country" in u:
            return {"metric": "delivered", "agg": "avg", "group_by": ["country_name"]}
        if "profit" in u and "vendor" in u:
            return {"metric": "profit", "agg": "sum", "group_by": ["vendor_name"]}
        if "operator" in u:
            return {"metric": "message_count", "agg": "count", "group_by": ["operator_name"]}
        if "transactional" in u:
            return {"metric": "delivered", "agg": "avg",
                    "filters": [{"field": "content_type", "op": "eq", "value": "transactional"}]}
        return {"error": "unhandled"}

    def tf(system: str, user: str) -> str:
        p = json.loads(user)
        rows = p.get("results", [])
        if rows:
            first = rows[0]
            label = next((v for k, v in first.items() if k != "value"), "")
            return f"Across {len(rows)} groups, {label} leads at {first.get('value')}."
        return "No data."
    return MockLLM(json_responses=jf, text_responses=tf)


def _mock_judge() -> MockLLM:
    # Deterministic stand-in: the agent mock is faithful and on-schema by design.
    return MockLLM(json_responses=lambda s, u: {
        "output_faithfulness": "pass", "faithfulness_reason": "answer cites returned values",
        "trajectory_validity": "pass", "trajectory_reason": "spec on-schema and on-target"})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="offline; no API key needed")
    ap.add_argument("--out", default=None, help="write traces+verdicts to this JSONL")
    args = ap.parse_args()
    cfg = _load_config()
    questions = cfg["question_set"]

    if args.mock:
        agent_llm: LLM = _mock_agent_llm()
        judge: LLM = _mock_judge()
    else:
        from ..agent.llm import GeminiLLM
        agent_llm = GeminiLLM()
        judge = GeminiLLM(model=cfg.get("judge_model", "gemini-2.5-flash"))

    agent = InsightAgent(backend=CDRBackend(DB), llm=agent_llm)
    traces = generate_traces(agent, questions)

    print(f"judging {len(traces)} reporting trajectories on 2 metrics\n")
    tallies = {"output_faithfulness": 0, "trajectory_validity": 0}
    records = []
    for tr in traces:
        v = judge_trace(judge, tr)
        for m in tallies:
            tallies[m] += 1 if v.get(m) == "pass" else 0
        records.append({"trace": tr, "verdict": v})
        mark = "PASS" if v.get("output_faithfulness") == "pass" and \
            v.get("trajectory_validity") == "pass" else "FAIL"
        print(f"  [{mark}] {tr['question'][:48]:<48} "
              f"faith={v.get('output_faithfulness')} traj={v.get('trajectory_validity')}")

    n = len(traces)
    print(f"\noutput_faithfulness: {tallies['output_faithfulness']}/{n}")
    print(f"trajectory_validity: {tallies['trajectory_validity']}/{n}")
    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(r, default=str) for r in records))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
