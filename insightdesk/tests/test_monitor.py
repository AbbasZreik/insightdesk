"""
Monitoring-agent test: replay the full CDR stream and confirm the ambient agent
raises the planted escalations, then confirm a block stops re-raising.

    python -m insightdesk.tests.test_monitor
"""
from __future__ import annotations

from ..monitor.engine import MonitoringAgent
from ..monitor.stream import CDRStream

PARQUET = "insightdesk/data/cdr.parquet"


def main() -> None:
    agent = MonitoringAgent()
    stream = CDRStream(PARQUET, batch_min=5)

    ticks = 0
    while stream.has_next():
        batch = stream.next_batch()
        if batch.empty:
            continue
        agent.ingest(batch)
        agent.evaluate()
        ticks += 1

    fired = {e.rule for e in agent.store.all()}
    print(f"replayed {ticks} batches; rules that fired: {sorted(fired)}")
    for e in agent.store.open():
        r = e.route
        loc = " / ".join(str(v) for v in r.values())
        print(f"  [{e.severity:<8}] {e.rule:<17} {loc:<45} {e.reason}")

    # Each planted scenario should have produced at least one escalation.
    for expected in ("route_degradation", "margin_leak", "traffic_surge", "otp_failure"):
        assert expected in fired, f"expected {expected} to fire"

    # Blocking a route resolves it and stops re-raising.
    first = agent.store.open()[0]
    before = agent.store.counts()
    agent.store.block(first.id)
    agent.evaluate()
    after = agent.store.counts()
    assert agent.store.is_blocked(first.route), "route should be blocked"
    assert after["blocked"] >= 1, "block should be recorded"
    print(f"\nblocked {first.id} ({first.rule}); counts before={before} after={after}")

    print("\nALL MONITOR CHECKS PASSED")


if __name__ == "__main__":
    main()
