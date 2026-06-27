"""
Eval runner for InsightDesk spec accuracy.

Runs every case in cases.py, compares the generated spec to the expected
properties (partial match), and reports pass rate per category. Guardrail cases
pass when the spec is correctly REJECTED.

Run offline (deterministic, no key):
    python -m insightdesk.eval.run_eval --mock
Run against your real model:
    export GEMINI_API_KEY=...
    python -m insightdesk.eval.run_eval
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from ..backends.base import SchemaInfo
from ..backends.duckdb_backend import DuckDBBackend
from ..agent.llm import LLM, MockLLM
from ..agent.spec_agent import SpecError, text_to_spec
from .cases import CASES, EvalCase

DB = "insightdesk/data/insightdesk.duckdb"


def _mock_llm() -> MockLLM:
    """Canned specs keyed to the eval questions (offline determinism)."""
    def json_fn(system: str, user: str) -> dict:
        u = user.lower()
        if "validation error" in u:
            return {"error": "cannot answer with this schema"}  # don't fake a fix
        if "cost per region" in u and "whatsapp" not in u:
            return {"metric": "cost", "agg": "sum", "group_by": ["region"]}
        if "each product" in u:
            return {"metric": "message_count", "agg": "sum", "group_by": ["product"]}
        if "overall" in u:
            return {"metric": "cost", "agg": "sum", "group_by": []}
        if "cost for sms" in u:
            return {"metric": "cost", "agg": "sum",
                    "filters": [{"field": "product", "op": "eq", "value": "SMS"}]}
        if "whatsapp" in u:
            return {"metric": "cost", "agg": "sum", "group_by": ["region"],
                    "filters": [{"field": "product", "op": "eq", "value": "WhatsApp"}]}
        if "europe" in u:
            return {"metric": "cost", "agg": "sum", "group_by": ["__time__"],
                    "granularity": "month",
                    "filters": [{"field": "region", "op": "eq", "value": "Europe"}]}
        if "daily message volume" in u:
            return {"metric": "message_count", "agg": "sum",
                    "group_by": ["__time__"], "granularity": "day"}
        if "top clients" in u:
            return {"metric": "cost", "agg": "sum", "group_by": ["client_id"]}
        if "country" in u:
            return {"metric": "cost", "agg": "sum", "group_by": ["country"]}
        if "salesperson" in u or "revenue" in u:
            return {"metric": "revenue", "agg": "avg", "group_by": ["salesperson"]}
        return {"error": "unhandled"}
    return MockLLM(json_responses=json_fn)


def _check(case: EvalCase, schema: SchemaInfo, llm: LLM) -> tuple[bool, str]:
    try:
        spec = text_to_spec(llm, schema, case.question).spec
    except SpecError as e:
        if case.must_error:
            return True, "correctly rejected"
        return False, f"unexpected rejection: {e}"

    if case.must_error:
        return False, "should have been rejected but produced a spec"

    if case.metric is not None and spec.metric != case.metric:
        return False, f"metric {spec.metric!r} != {case.metric!r}"
    if case.agg is not None and spec.agg != case.agg:
        return False, f"agg {spec.agg!r} != {case.agg!r}"
    if case.group_by is not None and set(spec.group_by) != case.group_by:
        return False, f"group_by {set(spec.group_by)} != {case.group_by}"
    if case.filters is not None:
        got = {(f.field, f.op, f.value) for f in spec.filters}
        missing = case.filters - got
        if missing:
            return False, f"missing filters {missing}"
    return True, "ok"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="offline mock model")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--threshold", type=float, default=0.8,
                    help="min overall pass rate for exit 0")
    args = ap.parse_args()

    if args.mock:
        llm: LLM = _mock_llm()
    else:
        from ..agent.llm import GeminiLLM
        llm = GeminiLLM()

    schema = DuckDBBackend(args.db).get_schema()

    by_cat: dict[str, list[bool]] = defaultdict(list)
    print(f"{'cat':<12} {'result':<6} question")
    print("-" * 70)
    for case in CASES:
        ok, detail = _check(case, schema, llm)
        by_cat[case.category].append(ok)
        mark = "PASS" if ok else "FAIL"
        print(f"{case.category:<12} {mark:<6} {case.question}"
              + ("" if ok else f"   <- {detail}"))

    print("-" * 70)
    total = [r for results in by_cat.values() for r in results]
    for cat, results in sorted(by_cat.items()):
        print(f"  {cat:<12} {sum(results)}/{len(results)}")
    rate = sum(total) / len(total)
    print(f"\nOVERALL: {sum(total)}/{len(total)}  ({rate:.0%})")
    sys.exit(0 if rate >= args.threshold else 1)


if __name__ == "__main__":
    main()
