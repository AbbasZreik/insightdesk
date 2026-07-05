"""
Monitoring rules — the "skills" the ambient agent evaluates on live traffic.

Each rule is a named, plain-language business rule with a machine-checkable
condition over a route window. Detection is deterministic; the model is only
used (optionally) to compile a NEW rule from plain English, reusing the same
skill-compiler idea as the reporting side.

Rule kinds:
  delivery_rate_below  - delivered/total under a threshold (route quality)
  volume_surge         - window volume far above the route's rolling baseline
  negative_margin      - sum(profit) at or below zero (loss-making route)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MonitorRule:
    name: str
    description: str
    kind: str                                  # delivery_rate_below | volume_surge | negative_margin
    scope: list[str]                           # route dimensions to group by
    params: dict[str, Any]
    severity: str = "high"
    recommended_action: str = "block route"
    min_sample: int = 30                       # ignore tiny-volume noise
    window_min: int = 15                       # trailing window (sim minutes)
    content_type: str | None = None            # restrict to promotional/transactional


# --------------------------------------------------------------------------
# Starter rule set (SMS-firewall style)
# --------------------------------------------------------------------------

from ..agent.skill_loader import load_monitor_rule_defs

# Monitoring rules are LOADED from the SKILL.md folder
# (skills/traffic-monitoring/assets/rules.json), not hardcoded.
RULES: list[MonitorRule] = [MonitorRule(**d) for d in load_monitor_rule_defs()]


def rules_by_name() -> dict[str, MonitorRule]:
    return {r.name: r for r in RULES}
