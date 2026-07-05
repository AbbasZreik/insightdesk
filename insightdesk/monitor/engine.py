"""
The ambient monitoring agent.

It is event-driven, not prompt-driven: as CDR batches arrive it maintains a
trailing window, evaluates every rule per route, and raises/updates escalations.
Detection is fully deterministic and reproducible; the agent decides WHAT is
wrong and recommends an action, but a human in the portal decides whether to
block. Blocked routes are skipped on subsequent ticks.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .escalation import EscalationStore, route_key
from .rules import RULES, MonitorRule


@dataclass
class MonitoringAgent:
    store: EscalationStore = field(default_factory=EscalationStore)
    rules: list[MonitorRule] = field(default_factory=lambda: list(RULES))
    _df: pd.DataFrame = field(default_factory=pd.DataFrame)
    _ema: dict = field(default_factory=dict)        # rule -> route_key -> baseline

    # -- ingest ------------------------------------------------------------
    def ingest(self, batch: pd.DataFrame) -> None:
        self._df = batch if self._df.empty else pd.concat([self._df, batch], ignore_index=True)

    @property
    def now(self):
        return self._df["created_ts"].max() if not self._df.empty else None

    # -- evaluate ----------------------------------------------------------
    def evaluate(self) -> list[dict]:
        if self._df.empty:
            return []
        now = self.now
        touched: list[dict] = []
        for rule in self.rules:
            window_start = now - pd.Timedelta(minutes=rule.window_min)
            sub = self._df[self._df["created_ts"] >= window_start]
            if rule.content_type:
                sub = sub[sub["content_type"] == rule.content_type]
            if sub.empty:
                continue
            grouped = sub.groupby(rule.scope, observed=True)
            for keys, g in grouped:
                route = dict(zip(rule.scope, keys if isinstance(keys, tuple) else (keys,)))
                n = len(g)
                esc = self._check(rule, route, g, n, now)
                if esc:
                    touched.append(esc.to_dict())
        return touched

    def _check(self, rule, route, g, n, now):
        ts = str(now)
        if rule.kind == "delivery_rate_below":
            if n < rule.min_sample:
                return None
            dr = float(g["delivered"].mean())
            if dr >= rule.params["threshold"]:
                return None
            reason = (f"Delivery rate {dr:.0%} over the last {rule.window_min} min "
                      f"on {n} messages (threshold {rule.params['threshold']:.0%}).")
            return self.store.upsert(rule.name, rule.severity, route,
                                     {"delivery_rate": round(dr, 3), "messages": n},
                                     rule.recommended_action, rule.description, reason, ts)
        if rule.kind == "negative_margin":
            if n < rule.min_sample:
                return None
            profit = float(g["profit"].sum())
            if profit > 0:
                return None
            reason = (f"Route is loss-making: total profit {profit:.2f} over {n} "
                      f"messages in the last {rule.window_min} min.")
            return self.store.upsert(rule.name, rule.severity, route,
                                     {"profit": round(profit, 2), "messages": n},
                                     rule.recommended_action, rule.description, reason, ts)
        if rule.kind == "volume_surge":
            rk = route_key(route)
            baseline = self._ema.setdefault(rule.name, {}).get(rk)
            fired = None
            if (baseline and n >= rule.min_sample and n > rule.params["factor"] * baseline):
                reason = (f"Volume {n} in the last {rule.window_min} min is "
                          f"{n / baseline:.1f}x the normal ~{baseline:.0f}.")
                fired = self.store.upsert(rule.name, rule.severity, route,
                                          {"messages": n, "baseline": round(baseline, 1),
                                           "x_normal": round(n / baseline, 1)},
                                          rule.recommended_action, rule.description, reason, ts)
            # update rolling baseline AFTER checking (slow alpha)
            self._ema[rule.name][rk] = n if baseline is None else 0.7 * baseline + 0.3 * n
            return fired
        return None

    # -- portal dashboard summary -----------------------------------------
    def window_summary(self, window_min: int = 15) -> dict:
        if self._df.empty:
            return {"messages": 0, "delivery_rate": None, "clock": None}
        now = self.now
        sub = self._df[self._df["created_ts"] >= now - pd.Timedelta(minutes=window_min)]
        return {
            "clock": str(now),
            "messages": int(len(sub)),
            "delivery_rate": round(float(sub["delivered"].mean()), 3) if len(sub) else None,
            "blocked_routes": len(self.store.blocked_routes()),
            "counts": self.store.counts(),
        }
