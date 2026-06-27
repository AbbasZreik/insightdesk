"""
Synthetic messaging/billing dataset for InsightDesk.

Generates a daily-grained fact table of messaging events plus a small client
dimension, with THREE reproducible planted anomalies you control:

  1. USAGE SPIKE   - one client's SMS volume surges ~8x for one week (abuse/fraud).
  2. REVENUE DIP   - one region's cost drops ~40% for one month (pricing bug / outage).
  3. CHURN         - one client stops sending after a cutoff date (status="churned").

Everything is driven by a fixed RNG seed and the ANOMALIES config, so the demo
is byte-for-byte reproducible and you decide exactly what the agent should catch.

Usage:
    python -m insightdesk.data.seed --out insightdesk/data --days 180 --seed 42
Outputs:
    events.parquet   (fact table)   |   clients.parquet (dimension)
    insightdesk.duckdb  (a ready-to-query DuckDB file with both tables)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Static domain
# ---------------------------------------------------------------------------

REGIONS = ["MENA", "Europe", "North America", "Asia"]
PRODUCTS = ["SMS", "WhatsApp", "Voice", "Email"]
UNIT_PRICE = {"SMS": 0.040, "WhatsApp": 0.020, "Voice": 0.150, "Email": 0.001}

CLIENTS = [
    ("C001", "Falcon Media", "MENA", "enterprise"),
    ("C002", "Cedar Retail", "MENA", "growth"),
    ("C003", "Nordwind GmbH", "Europe", "enterprise"),
    ("C004", "Hellas Travel", "Europe", "growth"),
    ("C005", "Aurora Bank", "Europe", "enterprise"),
    ("C006", "Liberty Foods", "North America", "growth"),
    ("C007", "Pioneer Health", "North America", "enterprise"),
    ("C008", "Summit Logistics", "North America", "starter"),
    ("C009", "Sakura Mobile", "Asia", "growth"),
    ("C010", "Tiger Pay", "Asia", "enterprise"),
]

# Baseline daily message volume per client plan (mean of a Poisson draw).
PLAN_BASE_VOLUME = {"enterprise": 1800, "growth": 600, "starter": 120}

# ---------------------------------------------------------------------------
# Anomaly configuration — edit these to control what the agent should find
# ---------------------------------------------------------------------------

ANOMALIES = {
    "usage_spike": {
        "client_id": "C001",     # Falcon Media
        "product": "SMS",
        "start_day": 90,
        "end_day": 96,           # inclusive
        "multiplier": 8.0,
    },
    "revenue_dip": {
        "region": "Europe",
        "month_index": 3,        # 0-based month from the start date
        "factor": 0.60,          # cost multiplied by this (≈40% drop)
    },
    "churn": {
        "client_id": "C008",     # Summit Logistics
        "cutoff_day": 120,       # no events on/after this day offset
    },
}


def generate(days: int = 180, seed: int = 42,
             start: dt.date = dt.date(2025, 1, 1)) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = [start + dt.timedelta(days=i) for i in range(days)]

    churn = ANOMALIES["churn"]
    churn_cutoff = start + dt.timedelta(days=churn["cutoff_day"])

    rows = []
    for cid, name, region, plan in CLIENTS:
        base = PLAN_BASE_VOLUME[plan]
        # Each client favours a couple of products; weights add realism.
        prod_weights = rng.dirichlet(np.ones(len(PRODUCTS)) * 1.5)
        for day_idx, date in enumerate(dates):
            # Churned client goes silent after the cutoff.
            if cid == churn["client_id"] and date >= churn_cutoff:
                continue
            # Mild weekly seasonality (weekends lighter).
            season = 0.7 if date.weekday() >= 5 else 1.0
            for p_idx, product in enumerate(PRODUCTS):
                mean = base * prod_weights[p_idx] * season
                if mean < 1:
                    continue
                volume = int(rng.poisson(mean))
                if volume <= 0:
                    continue

                # Anomaly 1: usage spike.
                spike = ANOMALIES["usage_spike"]
                if (cid == spike["client_id"] and product == spike["product"]
                        and spike["start_day"] <= day_idx <= spike["end_day"]):
                    volume = int(volume * spike["multiplier"])

                cost = volume * UNIT_PRICE[product]

                # Anomaly 2: revenue dip for a region in one month.
                dip = ANOMALIES["revenue_dip"]
                if region == dip["region"] and date.month == (
                    (start.month - 1 + dip["month_index"]) % 12 + 1
                ):
                    cost *= dip["factor"]

                rows.append({
                    "event_date": date.isoformat(),
                    "client_id": cid,
                    "region": region,
                    "product": product,
                    "message_count": volume,
                    "cost": round(cost, 4),
                })

    events = pd.DataFrame(rows)

    clients = pd.DataFrame(
        [{"client_id": c, "client_name": n, "region": r, "plan": p,
          "status": "churned" if c == churn["client_id"] else "active"}
         for c, n, r, p in CLIENTS]
    )
    return events, clients


def write_outputs(events: pd.DataFrame, clients: pd.DataFrame, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    events.to_parquet(os.path.join(out_dir, "events.parquet"), index=False)
    clients.to_parquet(os.path.join(out_dir, "clients.parquet"), index=False)

    # Also materialise a ready-to-query DuckDB file.
    import duckdb
    db_path = os.path.join(out_dir, "insightdesk.duckdb")
    if os.path.exists(db_path):
        os.remove(db_path)
    con = duckdb.connect(db_path)
    con.register("events_df", events)
    con.register("clients_df", clients)
    con.execute("CREATE TABLE events AS SELECT * FROM events_df")
    con.execute("CREATE TABLE clients AS SELECT * FROM clients_df")
    con.execute("ALTER TABLE events ALTER event_date TYPE DATE")
    con.close()
    return db_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic messaging/billing data.")
    ap.add_argument("--out", default="insightdesk/data")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    events, clients = generate(days=args.days, seed=args.seed)
    db_path = write_outputs(events, clients, args.out)

    print(f"events:  {len(events):>7,} rows  ->  {args.out}/events.parquet")
    print(f"clients: {len(clients):>7,} rows  ->  {args.out}/clients.parquet")
    print(f"duckdb:  {db_path}")
    print("\nPlanted anomalies:")
    print("  1. usage spike  -> client C001 (Falcon Media), SMS, days 90-96, x8")
    print("  2. revenue dip  -> region Europe, month index 3, x0.60")
    print("  3. churn        -> client C008 (Summit Logistics) silent after day 120")
    total = events["cost"].sum()
    print(f"\nTotal billed cost in dataset: {total:,.2f}")


if __name__ == "__main__":
    main()
