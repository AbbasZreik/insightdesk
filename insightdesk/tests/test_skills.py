"""
Anomaly-skill tests on the CDR data (offline, deterministic).

    python -m insightdesk.tests.test_skills
"""
from __future__ import annotations

from ..agent.llm import MockLLM
from ..agent.skills import (
    BUILTIN_SKILLS, apply_skill, compile_skill_from_instruction,
    resolve_skill, validate_rule,
)
from ..backends.base import AggregationSpec, Filter
from ..backends.cdr_backend import CDRBackend

DB = "insightdesk/data/cdr.duckdb"


def main() -> None:
    be = CDRBackend(DB)
    schema = be.get_schema()

    # Delivery rate by country (Nigeria is the clear underperformer).
    by_country = be.run_aggregation(AggregationSpec(
        metric="delivered", agg="avg", group_by=["country_name"]))

    print("== 1. built-in 'sharp_drop' flags a short dip against a stable baseline ==")
    # Crafted series isolates the skill logic (sustained regime changes are caught
    # by the monitoring rules' thresholds, not by median-deviation).
    series = [{"bucket": f"t{i}", "value": v}
              for i, v in enumerate([100, 98, 102, 60, 101, 99])]
    drops = apply_skill(BUILTIN_SKILLS["sharp_drop"], series, ["__time__"])
    print("  flagged:", [(a["label"], a.get("deviation_pct")) for a in drops])
    assert any(a["direction"] == "drop" for a in drops), "should flag the dip"

    print("\n== 2. instruction -> compiled skill (mock model) ==")
    mock = MockLLM(json_responses=lambda s, u: {
        "type": "relative_extreme", "reference": "max", "op": "lt", "ratio": 0.8,
        "description": "Countries delivering under 80% of the best country"})
    skill = compile_skill_from_instruction(
        mock, "under_80", "flag countries delivering below 80% of the top country", schema)
    print("  compiled rule:", skill.rule)
    flagged = apply_skill(skill, by_country, ["country_name"])
    print("  flagged:", [(a["label"], round(a["value"], 3)) for a in flagged])
    assert any(f["label"] == "Nigeria" for f in flagged), "Nigeria is < 80% of the top"

    print("\n== 3. resolve_skill: built-in name vs instruction ==")
    assert resolve_skill("auto").name == "auto"
    print("  'auto' ->", resolve_skill("auto").description[:42], "...")

    print("\n== 4. validate_rule rejects a bad rule ==")
    try:
        validate_rule({"type": "pct_deviation", "pct": 50})
        raise AssertionError("should have rejected pct=50")
    except ValueError as e:
        print("  rejected:", e)

    print("\nALL SKILL CHECKS PASSED")


if __name__ == "__main__":
    main()
