"""
Ambient-agent simulator / validator.

Streams the CDR dataset into the monitoring agent in time order, one batch at a
time, and prints what the agent does on each tick — so you can watch (and check)
the ambient behavior without the web UI. This is the same CDRStream + Monitoring
Agent the portal uses; here it just runs headless with live console output.

    python -m insightdesk.monitor.simulate
    python -m insightdesk.monitor.simulate --batch-min 5 --speed 0.3
    python -m insightdesk.monitor.simulate --quiet         # only escalations + summary

Flags:
    --batch-min N   simulated minutes per batch (default 5)
    --speed S       seconds to sleep between batches, for a real-time feel (default 0)
    --parquet PATH  CDR parquet to replay
"""
from __future__ import annotations

import argparse
import time

from .engine import MonitoringAgent
from .stream import CDRStream

SEV_TAG = {"critical": "!!", "high": "! ", "medium": "~ ", "low": ". "}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default="insightdesk/data/cdr.parquet")
    ap.add_argument("--batch-min", type=int, default=5)
    ap.add_argument("--speed", type=float, default=0.0)
    ap.add_argument("--quiet", action="store_true", help="suppress per-tick status")
    args = ap.parse_args()

    agent = MonitoringAgent()
    stream = CDRStream(args.parquet, batch_min=args.batch_min)
    seen: set[str] = set()
    tick = 0

    print(f"streaming {stream.start}  ->  {stream.end}  "
          f"(batch = {args.batch_min} sim-min)\n")

    while stream.has_next():
        batch = stream.next_batch()
        if batch.empty:
            continue
        tick += 1
        agent.ingest(batch)
        agent.evaluate()
        summ = agent.window_summary()

        if not args.quiet:
            dr = summ["delivery_rate"]
            clock = (summ["clock"] or "")[11:19]
            print(f"tick {tick:>2} | {clock} | +{len(batch):>4} cdrs | "
                  f"window dr {dr*100:4.0f}% | open {summ['counts']['open']}"
                  if dr is not None else f"tick {tick:>2}")

        # print escalations the first time we see them
        for e in agent.store.open():
            if e.id not in seen:
                seen.add(e.id)
                route = " / ".join(str(v) for v in e.route.values())
                print(f"   {SEV_TAG.get(e.severity,'  ')}[{e.severity:<8}] "
                      f"{e.rule:<17} {route:<42} {e.reason}")
        if args.speed:
            time.sleep(args.speed)

    print("\n--- summary ---")
    fired: dict[str, int] = {}
    for e in agent.store.all():
        fired[e.rule] = fired.get(e.rule, 0) + 1
    for rule, n in sorted(fired.items()):
        print(f"  {rule:<18} {n} route(s)")
    print(f"  totals: {agent.store.counts()}")
    print(f"  {tick} batches replayed over {args.batch_min}-min windows")


if __name__ == "__main__":
    main()
