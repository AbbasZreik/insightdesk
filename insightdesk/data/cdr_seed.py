"""
Synthetic CDR (message detail record) generator for InsightDesk / SMS traffic.

Produces transaction-level A2P SMS records over a simulated window, with four
planted anomaly scenarios the monitoring agent is meant to catch:

  A. ROUTE DEGRADATION  - one client x country x operator route's delivery rate
                          collapses partway through (bad supplier route).
  B. FRAUD SURGE        - one promotional sender's volume to Nigeria spikes ~8x
                          (spam / grey-route abuse).
  C. MARGIN LEAK        - one vendor route turns loss-making (cost > rate).
  D. OTP FAILURE        - transactional/OTP traffic to Egypt drops in delivery
                          (critical, SLA-bound).

Output:
  cdr.parquet   - time-ordered records (the monitoring agent replays this)
  cdr.duckdb    - table `cdr` (the reporting agent queries this)

Run:  python -m insightdesk.data.cdr_seed --out insightdesk/data --minutes 180
"""
from __future__ import annotations

import argparse
import datetime as dt
import os

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Reference dimensions
# ---------------------------------------------------------------------------

CLIENTS = [("CL01", "Falcon Telecom"), ("CL02", "Cedar Pay"), ("CL03", "Aurora Bank"),
           ("CL04", "Nimbus Retail"), ("CL05", "Pioneer Health"), ("CL06", "Tiger Wallet")]
VENDORS = [("VN01", "GlobalRoute"), ("VN02", "SignalLink"), ("VN03", "OmniSMS"),
           ("VN04", "CheapHop"), ("VN05", "PrimeCarrier")]

COUNTRIES = {1: "Saudi Arabia", 2: "UAE", 3: "Egypt", 4: "India", 5: "Nigeria"}
OPERATORS = {
    1: [(11, "STC"), (12, "Mobily")], 2: [(21, "Etisalat"), (22, "du")],
    3: [(31, "Vodafone EG"), (32, "Orange EG")], 4: [(41, "Airtel"), (42, "Jio")],
    5: [(51, "MTN NG"), (52, "Glo")],
}
# Per-country client rate and vendor cost (USD per message).
PRICING = {1: (0.030, 0.022), 2: (0.028, 0.020), 3: (0.012, 0.008),
           4: (0.0080, 0.0058), 5: (0.022, 0.015)}

PROMO_SENDERS = ["MEGADEAL", "SHOPNOW", "QUICKWIN", "FLASHSALE"]
OTP_SENDERS = ["VerifyPro", "BankOTP", "SecureAuth", "HealthID"]

# Baseline routes: (client, vendor, country, operator, msgs/min, delivery_rate).
BASE_ROUTES = [
    ("CL01", "VN01", 1, 11, 14, 0.95), ("CL01", "VN02", 1, 12, 10, 0.94),
    ("CL03", "VN01", 2, 21, 12, 0.96), ("CL04", "VN03", 2, 22, 9, 0.93),
    ("CL02", "VN02", 3, 31, 11, 0.92), ("CL05", "VN03", 3, 32, 8, 0.95),
    ("CL02", "VN04", 4, 41, 16, 0.90), ("CL06", "VN05", 4, 42, 13, 0.94),
    ("CL04", "VN04", 5, 51, 10, 0.91), ("CL06", "VN01", 5, 52, 9, 0.93),
]

STATUS_FAIL = ["failed", "rejected", "expired"]


def _status(rng, dr):
    return "delivered" if rng.random() < dr else rng.choice(STATUS_FAIL, p=[0.6, 0.25, 0.15])


def generate(minutes: int = 180, seed: int = 7,
             start: dt.datetime = dt.datetime(2026, 6, 1, 8, 0, 0)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    cid = 0

    # Scenario activation (minute offsets)
    DEGRADE_AT, SURGE_AT, MARGIN_AT, OTP_AT = 70, 90, 50, 110

    for t in range(minutes):
        now = start + dt.timedelta(minutes=t)
        for (client, vendor, country, operator, base_lambda, base_dr) in BASE_ROUTES:
            lam = base_lambda
            dr = base_dr
            rate, cost = PRICING[country]

            # Scenario A: route degradation on CL02 x India x Airtel after DEGRADE_AT
            if client == "CL02" and country == 4 and operator == 41 and t >= DEGRADE_AT:
                dr = 0.42
            # Scenario C: margin leak on vendor VN04 x India after MARGIN_AT
            if vendor == "VN04" and country == 4 and t >= MARGIN_AT:
                cost = rate * 1.15            # cost now exceeds rate

            n = rng.poisson(lam)
            for _ in range(n):
                otp = rng.random() < 0.35
                sender = rng.choice(OTP_SENDERS if otp else PROMO_SENDERS)
                content = "transactional" if otp else "promotional"
                rrate, rcost = rate, cost

                # Scenario D: OTP failure to Egypt after OTP_AT
                row_dr = dr
                if otp and country == 3 and t >= OTP_AT:
                    row_dr = 0.58

                status = _status(rng, row_dr)
                delivered = int(status == "delivered")
                created = now + dt.timedelta(seconds=int(rng.integers(0, 60)))
                latency = int(rng.integers(2, 25)) if delivered else int(rng.integers(20, 90))
                delivery = created + dt.timedelta(seconds=latency)
                profit = round(rrate - rcost, 6)
                cid += 1
                rows.append((
                    f"CDR{cid:07d}", created, delivery, client, _name(CLIENTS, client),
                    vendor, _name(VENDORS, vendor), country, COUNTRIES[country],
                    operator, _op_name(country, operator), sender, content,
                    status, delivered, round(rrate, 6), round(rcost, 6), profit, latency,
                ))

        # Scenario B: fraud surge - QUICKWIN promo blast to Nigeria/MTN after SURGE_AT
        if t >= SURGE_AT:
            rate, cost = PRICING[5]
            for _ in range(rng.poisson(80)):
                status = _status(rng, 0.55)         # spammy traffic, low delivery
                delivered = int(status == "delivered")
                created = now + dt.timedelta(seconds=int(rng.integers(0, 60)))
                latency = int(rng.integers(2, 60))
                cid += 1
                rows.append((
                    f"CDR{cid:07d}", created, created + dt.timedelta(seconds=latency),
                    "CL04", _name(CLIENTS, "CL04"), "VN04", _name(VENDORS, "VN04"),
                    5, COUNTRIES[5], 51, _op_name(5, 51), "QUICKWIN", "promotional",
                    status, delivered, round(rate, 6), round(cost, 6),
                    round(rate - cost, 6), latency,
                ))

    cols = ["cdr_id", "created_ts", "delivery_ts", "client_id", "client_name",
            "vendor_id", "vendor_name", "country_id", "country_name", "operator_id",
            "operator_name", "sender", "content_type", "delivery_status", "delivered",
            "rate", "cost", "profit", "latency_sec"]
    df = pd.DataFrame(rows, columns=cols).sort_values("created_ts").reset_index(drop=True)
    return df


def _name(pairs, code):
    return dict(pairs)[code]


def _op_name(country, operator):
    return dict(OPERATORS[country])[operator]


def write_outputs(df: pd.DataFrame, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    df.to_parquet(os.path.join(out_dir, "cdr.parquet"), index=False)
    import duckdb
    db = os.path.join(out_dir, "cdr.duckdb")
    if os.path.exists(db):
        os.remove(db)
    con = duckdb.connect(db)
    con.register("cdr_df", df)
    con.execute("CREATE TABLE cdr AS SELECT * FROM cdr_df")
    con.close()
    return db


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="insightdesk/data")
    ap.add_argument("--minutes", type=int, default=180)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    df = generate(minutes=args.minutes, seed=args.seed)
    db = write_outputs(df, args.out)
    print(f"cdr rows: {len(df):,}  ->  {args.out}/cdr.parquet  &  {db}")
    print(f"window: {df.created_ts.min()}  ..  {df.created_ts.max()}")
    print(f"overall delivery rate: {df.delivered.mean():.1%}")
    print("planted scenarios: route degradation (CL02/India/Airtel), fraud surge "
          "(QUICKWIN/Nigeria), margin leak (VN04/India), OTP failure (Egypt)")


if __name__ == "__main__":
    main()
