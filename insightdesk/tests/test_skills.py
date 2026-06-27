"""
Anomaly-skill tests (offline, deterministic).

    python -m insightdesk.tests.test_skills
"""
from __future__ import annotations

from ..agent.llm import MockLLM
from ..agent.skills import (
    BUILTIN_SKILLS, apply_skill, compile_skill_from_instruction,
    resolve_skill, validate_rule,
)
from ..backends.base import AggregationSpec, Filter
from ..backends.duckdb_backend import DuckDBBackend

DB = "insightdesk/data/insightdesk.duckdb"


def main() -> None:
    be = DuckDBBackend(DB)
    schema = be.get_schema()

    # Europe monthly series (has the planted April dip).
    europe = be.run_aggregation(AggregationSpec(
        metric="cost", agg="sum", group_by=["__time__"], granularity="month",
        filters=[Filter("region", "eq", "Europe")],
        order_by="bucket", order_desc=False))
    # Cost per region (categorical).
    regions = be.run_aggregation(AggregationSpec(
        metric="cost", agg="sum", group_by=["region"]))

    print("== 1. built-in 'sharp_drop' flags the April revenue dip ==")
    drops = apply_skill(BUILTIN_SKILLS["sharp_drop"], europe, ["__time__"])
    print("  flagged:", [(str(a["label"])[:7], a["deviation_pct"]) for a in drops])
    assert any(a["direction"] == "drop" for a in drops), "should flag the dip"

    print("\n== 2. instruction -> compiled skill (mock model) ==")
    # Analyst says: flag regions billing less than 80% of the top region.
    mock = MockLLM(json_responses=lambda s, u: {
        "type": "relative_extreme", "reference": "max", "op": "lt", "ratio": 0.8,
        "description": "Regions billing under 80% of the top region",
    })
    skill = compile_skill_from_instruction(
        mock, "under_80", "flag regions billing less than 80% of the top region", schema)
    print("  compiled rule:", skill.rule)
    flagged = apply_skill(skill, regions, ["region"])
    print("  flagged:", [(a["label"], round(a["value"], 0)) for a in flagged])
    assert {f["label"] for f in flagged} == {"Asia", "Europe"}, "Asia+Europe < 80% of NA"

    print("\n== 3. resolve_skill: built-in name vs instruction ==")
    assert resolve_skill("auto").name == "auto"
    print("  'auto' resolved to built-in:", resolve_skill("auto").description[:40], "...")

    print("\n== 4. validate_rule rejects a bad rule ==")
    try:
        validate_rule({"type": "pct_deviation", "pct": 50})  # pct must be a fraction
        raise AssertionError("should have rejected pct=50")
    except ValueError as e:
        print("  rejected:", e)

    print("\nALL SKILL CHECKS PASSED")


if __name__ == "__main__":
    main()
