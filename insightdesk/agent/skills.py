"""
Anomaly Skills (Day 3 "Skills" concept).

A *skill* is a named, reusable anomaly detector defined by two things:
  - a plain-language description (what it flags), and
  - a machine-checkable rule (how it flags).

Skills compose the agent's behavior without retraining or new code: the built-in
library covers the statistical detectors, and an analyst can define a NEW skill
just by describing it ("flag any month where cost drops more than 30% below
normal"). That instruction is compiled ONCE by the model into a structured rule
(compile_skill_from_instruction); from then on the detection is fully
deterministic. The model never decides per-point what is an anomaly — it only
turns an instruction into a rule, exactly as it turns a question into a query.

Rule types:
  auto            pick zscore (time series) or iqr (groups) automatically
  zscore          robust modified z-score on a time series; param: thresh
  iqr             IQR outlier rule across categorical groups
  pct_deviation   flag points deviating from a baseline by > pct
                  params: baseline ("median"|"mean"), direction
                  ("drop"|"spike"|"both"), pct (0-1)
  absolute        flag points beyond a fixed value; params: op ("gt"|"lt"), value
  relative_extreme flag points relative to the max/min; params: reference
                  ("max"|"min"), op ("gt"|"lt"), ratio (0-1+)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from statistics import mean, median
from typing import Any

from ..backends.base import SchemaInfo
from .anomaly import _iqr_anomalies, _modified_zscore_anomalies
from .llm import LLM

VALID_RULE_TYPES = {
    "auto", "zscore", "iqr", "pct_deviation", "absolute", "relative_extreme",
}


@dataclass
class AnomalySkill:
    name: str
    description: str
    rule: dict[str, Any]


# --------------------------------------------------------------------------
# Rule validation (guardrail: a compiled or hand-written rule must be sane)
# --------------------------------------------------------------------------

def validate_rule(rule: dict[str, Any]) -> None:
    t = rule.get("type")
    if t not in VALID_RULE_TYPES:
        raise ValueError(f"Unknown rule type {t!r}. Allowed: {sorted(VALID_RULE_TYPES)}")
    if t == "pct_deviation":
        if rule.get("baseline", "median") not in {"median", "mean"}:
            raise ValueError("pct_deviation.baseline must be 'median' or 'mean'")
        if rule.get("direction", "both") not in {"drop", "spike", "both"}:
            raise ValueError("pct_deviation.direction must be drop/spike/both")
        pct = rule.get("pct")
        if not isinstance(pct, (int, float)) or not (0 < pct < 10):
            raise ValueError("pct_deviation.pct must be a fraction like 0.30")
    elif t == "absolute":
        if rule.get("op") not in {"gt", "lt"}:
            raise ValueError("absolute.op must be 'gt' or 'lt'")
        if not isinstance(rule.get("value"), (int, float)):
            raise ValueError("absolute.value must be a number")
    elif t == "relative_extreme":
        if rule.get("reference", "max") not in {"max", "min"}:
            raise ValueError("relative_extreme.reference must be 'max' or 'min'")
        if rule.get("op") not in {"gt", "lt"}:
            raise ValueError("relative_extreme.op must be 'gt' or 'lt'")
        if not isinstance(rule.get("ratio"), (int, float)):
            raise ValueError("relative_extreme.ratio must be a number")


# --------------------------------------------------------------------------
# Applying a skill to query results
# --------------------------------------------------------------------------

def _label_key(rows: list[dict], group_by: list[str]) -> str:
    if rows and "bucket" in rows[0]:
        return "bucket"
    return group_by[0] if group_by else "label"


def _tag(items: list[dict], skill: AnomalySkill, reason: str) -> list[dict]:
    for it in items:
        it.setdefault("skill", skill.name)
        it.setdefault("reason", reason)
    return items


def apply_skill(skill: AnomalySkill, rows: list[dict[str, Any]],
                group_by: list[str]) -> list[dict]:
    if not rows:
        return []
    rule = skill.rule
    t = rule["type"]
    lk = _label_key(rows, group_by)
    is_time = "__time__" in group_by or (rows and "bucket" in rows[0])

    if t == "auto":
        if is_time:
            return _tag(_modified_zscore_anomalies(rows, "value", lk, rule.get("thresh", 3.5)),
                        skill, "statistical outlier (robust z-score)")
        return _tag(_iqr_anomalies(rows, "value", lk), skill, "statistical outlier (IQR)")
    if t == "zscore":
        return _tag(_modified_zscore_anomalies(rows, "value", lk, rule.get("thresh", 3.5)),
                    skill, "robust z-score outlier")
    if t == "iqr":
        return _tag(_iqr_anomalies(rows, "value", lk), skill, "IQR outlier")

    vals = [r["value"] for r in rows if r.get("value") is not None]
    if not vals:
        return []

    if t == "pct_deviation":
        base = median(vals) if rule.get("baseline", "median") == "median" else mean(vals)
        if base == 0:
            return []
        pct = rule["pct"]
        direction = rule.get("direction", "both")
        out = []
        for r in rows:
            v = r.get("value")
            if v is None:
                continue
            dev = (v - base) / base
            hit = ((direction in ("drop", "both") and dev <= -pct) or
                   (direction in ("spike", "both") and dev >= pct))
            if hit:
                out.append({"label": r.get(lk), "value": v,
                            "direction": "drop" if dev < 0 else "spike",
                            "deviation_pct": round(dev * 100, 1)})
        return _tag(out, skill, f"{int(pct*100)}% deviation from {rule.get('baseline','median')}")

    if t == "absolute":
        op, thr = rule["op"], rule["value"]
        out = [{"label": r.get(lk), "value": r["value"],
                "direction": "high" if op == "gt" else "low"}
               for r in rows if r.get("value") is not None
               and ((op == "gt" and r["value"] > thr) or (op == "lt" and r["value"] < thr))]
        return _tag(out, skill, f"value {op} {thr}")

    if t == "relative_extreme":
        ref = max(vals) if rule.get("reference", "max") == "max" else min(vals)
        op, ratio = rule["op"], rule["ratio"]
        threshold = ref * ratio
        out = [{"label": r.get(lk), "value": r["value"],
                "direction": "high" if op == "gt" else "low"}
               for r in rows if r.get("value") is not None
               and ((op == "gt" and r["value"] > threshold) or
                    (op == "lt" and r["value"] < threshold))]
        return _tag(out, skill, f"value {op} {ratio}\u00D7 {rule.get('reference','max')}")

    return []


# --------------------------------------------------------------------------
# Built-in skill library
# --------------------------------------------------------------------------

BUILTIN_SKILLS: dict[str, AnomalySkill] = {
    "auto": AnomalySkill("auto", "Automatic statistical detection (z-score on "
                         "trends, IQR on groups).", {"type": "auto"}),
    "sharp_drop": AnomalySkill("sharp_drop", "Flag any point that falls 30% or "
                               "more below the typical level.",
                               {"type": "pct_deviation", "baseline": "median",
                                "direction": "drop", "pct": 0.30}),
    "sharp_spike": AnomalySkill("sharp_spike", "Flag any point at least double "
                                "the typical level.",
                                {"type": "pct_deviation", "baseline": "median",
                                 "direction": "spike", "pct": 1.0}),
    "underperformer": AnomalySkill("underperformer", "Flag any group billing "
                                   "less than half of the top group.",
                                   {"type": "relative_extreme", "reference": "max",
                                    "op": "lt", "ratio": 0.5}),
}


# --------------------------------------------------------------------------
# Compiling a skill from a natural-language instruction (model used ONCE)
# --------------------------------------------------------------------------

def _compiler_system(schema: SchemaInfo) -> str:
    return f"""\
You convert a plain-language anomaly definition into a JSON rule that flags
points in a result series. Metrics available: {', '.join(schema.metrics)}.

Return ONLY a JSON object: {{"type": <type>, ...params, "description": <text>}}.
Rule types and params:
- pct_deviation: baseline ("median"|"mean"), direction ("drop"|"spike"|"both"),
  pct (fraction, e.g. 0.30 for 30%).  Use for "drops/rises more than X%".
- absolute: op ("gt"|"lt"), value (number).  Use for "more/less than N".
- relative_extreme: reference ("max"|"min"), op ("gt"|"lt"), ratio (e.g. 0.5).
  Use for "less than half the top", "within X of the highest".
- zscore: thresh (default 3.5).  Use for generic "unusual" on a trend.
- iqr.   Use for generic "outlier" across groups.
Pick the single best-fitting type. Return raw JSON only, no markdown."""


def compile_skill_from_instruction(llm: LLM, name: str, instruction: str,
                                   schema: SchemaInfo) -> AnomalySkill:
    raw = llm.generate_json(_compiler_system(schema), instruction)
    description = raw.pop("description", instruction)
    validate_rule(raw)
    return AnomalySkill(name=name, description=description, rule=raw)


def resolve_skill(name_or_instruction: str, llm: LLM | None = None,
                  schema: SchemaInfo | None = None) -> AnomalySkill:
    """A built-in name returns that skill; anything else is compiled as an
    instruction (requires llm + schema)."""
    if name_or_instruction in BUILTIN_SKILLS:
        return BUILTIN_SKILLS[name_or_instruction]
    if llm is None or schema is None:
        raise ValueError(
            f"{name_or_instruction!r} is not a built-in skill; "
            f"compiling an instruction needs an llm and schema."
        )
    return compile_skill_from_instruction(llm, "custom", name_or_instruction, schema)
