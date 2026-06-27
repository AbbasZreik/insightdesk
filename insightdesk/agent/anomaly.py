"""
Deterministic anomaly detection over aggregation results.

Deliberately NOT an LLM job: the model narrates anomalies, but it must never
decide what counts as one — that has to be reproducible and defensible. This is
the InsightDesk differentiator on top of the required course concepts, and it's
what makes the agent beat a static dashboard: a yearly total hides the Europe
dip, but the monthly series exposes it.

Two methods, chosen by the shape of the result:
  - time series (group_by includes "__time__"): rolling/global z-score on the
    metric; points beyond `z_thresh` standard deviations are flagged.
  - categorical groups: IQR outlier rule across the group values.
"""
from __future__ import annotations

from statistics import median, pstdev
from typing import Any


def _modified_zscore_anomalies(rows: list[dict], value_key: str, label_key: str,
                               thresh: float) -> list[dict]:
    """Robust modified z-score (Iglewicz-Hoaglin): uses the median and the
    median absolute deviation, so a single spike/dip can't hide by inflating
    the standard deviation. Much more reliable than mean-based z on short series.
    """
    vals = [r[value_key] for r in rows if r.get(value_key) is not None]
    if len(vals) < 4:
        return []
    med = median(vals)
    mad = median([abs(v - med) for v in vals])
    # Fall back to std-based scaling when MAD collapses (many identical values).
    scale = mad if mad else (pstdev(vals) * 0.7979 or 1e-9)

    out = []
    for r in rows:
        v = r.get(value_key)
        if v is None:
            continue
        mz = 0.6745 * (v - med) / scale
        if abs(mz) >= thresh:
            out.append({
                "label": r.get(label_key),
                "value": v,
                "mod_zscore": round(mz, 2),
                "direction": "spike" if mz > 0 else "drop",
                "baseline_median": round(med, 2),
            })
    return out


def _iqr_anomalies(rows: list[dict], value_key: str, label_key: str) -> list[dict]:
    vals = sorted(r[value_key] for r in rows if r.get(value_key) is not None)
    n = len(vals)
    if n < 4:
        return []

    def q(p: float) -> float:
        idx = p * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        return vals[lo] + (vals[hi] - vals[lo]) * (idx - lo)

    q1, q3 = q(0.25), q(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return []
    lo_b, hi_b = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    out = []
    for r in rows:
        v = r.get(value_key)
        if v is None:
            continue
        if v < lo_b or v > hi_b:
            out.append({
                "label": r.get(label_key),
                "value": v,
                "direction": "high" if v > hi_b else "low",
                "iqr_bounds": [round(lo_b, 2), round(hi_b, 2)],
            })
    return out


def detect_anomalies(rows: list[dict[str, Any]], group_by: list[str],
                     thresh: float = 3.5) -> list[dict]:
    """Pick the right method from the spec's group_by and return flagged points."""
    if not rows:
        return []
    is_time = "__time__" in group_by or "bucket" in rows[0]
    label_key = "bucket" if "bucket" in rows[0] else (group_by[0] if group_by else "label")
    if is_time:
        return _modified_zscore_anomalies(rows, "value", label_key, thresh)
    if group_by:
        return _iqr_anomalies(rows, "value", label_key)
    return []
