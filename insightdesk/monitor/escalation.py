"""
Escalations and the route blocklist for the monitoring portal.

An escalation is what the ambient agent raises when a rule fires on a route:
it names the route, the rule, severity, the evidence, and the recommended
action, and carries a status the human in the portal moves (open -> blocked /
dismissed). The blocklist records routes a human has blocked so the agent stops
re-raising them (and, in a real system, the gateway stops sending them).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def route_key(route: dict[str, Any]) -> str:
    return "|".join(f"{k}={route[k]}" for k in sorted(route))


@dataclass
class Escalation:
    id: str
    rule: str
    severity: str                      # low | medium | high | critical
    route: dict[str, Any]
    metrics: dict[str, Any]
    recommended_action: str
    description: str
    reason: str
    status: str = "open"               # open | blocked | dismissed
    opened_at: str = ""
    updated_at: str = ""

    @property
    def key(self) -> str:
        return f"{self.rule}::{route_key(self.route)}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["route_key"] = route_key(self.route)
        return d


class EscalationStore:
    def __init__(self) -> None:
        self._by_key: dict[str, Escalation] = {}
        self._by_id: dict[str, Escalation] = {}
        self._blocklist: set[str] = set()      # route_key strings
        self._seq = 0

    # -- blocklist ---------------------------------------------------------
    def is_blocked(self, route: dict[str, Any]) -> bool:
        return route_key(route) in self._blocklist

    def blocked_routes(self) -> list[dict[str, Any]]:
        return [e.route for e in self._by_id.values() if e.status == "blocked"]

    # -- upsert (called by the engine) ------------------------------------
    def upsert(self, rule: str, severity: str, route: dict, metrics: dict,
               action: str, description: str, reason: str, ts: str) -> Escalation | None:
        if route_key(route) in self._blocklist:
            return None
        k = f"{rule}::{route_key(route)}"
        existing = self._by_key.get(k)
        if existing and existing.status != "open":
            return None                          # dismissed/blocked: leave it
        if existing:
            existing.metrics = metrics
            existing.reason = reason
            existing.severity = severity
            existing.updated_at = ts
            return existing
        self._seq += 1
        esc = Escalation(id=f"ESC{self._seq:04d}", rule=rule, severity=severity,
                         route=route, metrics=metrics, recommended_action=action,
                         description=description, reason=reason,
                         opened_at=ts, updated_at=ts)
        self._by_key[k] = esc
        self._by_id[esc.id] = esc
        return esc

    # -- portal actions ----------------------------------------------------
    def block(self, esc_id: str) -> Escalation | None:
        esc = self._by_id.get(esc_id)
        if esc:
            esc.status = "blocked"
            self._blocklist.add(route_key(esc.route))
        return esc

    def dismiss(self, esc_id: str) -> Escalation | None:
        esc = self._by_id.get(esc_id)
        if esc:
            esc.status = "dismissed"
        return esc

    # -- views -------------------------------------------------------------
    def open(self) -> list[Escalation]:
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        return sorted([e for e in self._by_id.values() if e.status == "open"],
                      key=lambda e: order.get(e.severity, 9))

    def all(self) -> list[Escalation]:
        return list(self._by_id.values())

    def counts(self) -> dict[str, int]:
        c = {"open": 0, "blocked": 0, "dismissed": 0}
        for e in self._by_id.values():
            c[e.status] = c.get(e.status, 0) + 1
        return c
