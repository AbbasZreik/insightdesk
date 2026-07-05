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

RULES: list[MonitorRule] = [
    MonitorRule(
        name="route_degradation",
        description="Flag a client route whose delivery rate drops below 65%.",
        kind="delivery_rate_below",
        scope=["client_id", "country_name", "operator_name"],
        params={"threshold": 0.65},
        severity="high", recommended_action="block / reroute", min_sample=40,
    ),
    MonitorRule(
        name="otp_failure",
        description="Flag OTP/transactional traffic with delivery rate below 80%.",
        kind="delivery_rate_below",
        scope=["country_name", "operator_name"],
        params={"threshold": 0.80},
        severity="critical", recommended_action="urgent reroute", min_sample=25,
        content_type="transactional",
    ),
    MonitorRule(
        name="traffic_surge",
        description="Flag a sender whose volume to a country spikes far above its "
                    "normal level (possible spam / grey route).",
        kind="volume_surge",
        scope=["sender", "country_name"],
        params={"factor": 4.0},
        severity="medium", recommended_action="throttle / review", min_sample=50,
    ),
    MonitorRule(
        name="margin_leak",
        description="Flag a vendor route that is losing money (cost exceeds rate).",
        kind="negative_margin",
        scope=["vendor_name", "country_name", "operator_name"],
        params={},
        severity="high", recommended_action="reprice / block", min_sample=30,
    ),
]


def rules_by_name() -> dict[str, MonitorRule]:
    return {r.name: r for r in RULES}
