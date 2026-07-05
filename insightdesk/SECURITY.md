# Security Architecture — InsightDesk (SMS Traffic Intelligence)

Security is a first-class concern here because the system is itself an
abuse/fraud-detection product, and because agents that touch real operations are
a real attack surface. This document records the threat model (STRIDE), the
controls, and the "paved road" of secure conventions the codebase follows.

## 1. Core security principle

**The model does language; it never takes consequential action.** The LLM turns
questions into validated query specs and narrates results. It does not write SQL,
does not compute the numbers it reports, and **cannot change any state**. Every
consequential action — blocking or dismissing a route — is performed by a human
in the portal. The agent observes and recommends; a person decides.

## 2. Trust boundaries

```
  user / MCP client        ── untrusted input ──►  reporting agent (read-only)
  CDR data store           ── read-only ───────►   backend, MCP server
  ambient monitor          ── deterministic ────►  escalations (recommendations)
  human operator           ── only actor ───────►  block / dismiss (state change)
```

The agent and the MCP server sit entirely on the read side of every boundary.
The only path that mutates state (the blocklist) is gated behind a human click.

## 3. STRIDE threat model

| Threat | Vector | Control |
|---|---|---|
| **Spoofing** | Forged request to act on a route | No action API for the agent/MCP; block requires a human in the authenticated portal session. |
| **Tampering** | Malicious query mutating data | Backend opens the store **read-only**; only parameterised aggregations are possible; no DDL/DML path exists. |
| **Repudiation** | "The agent did X, unprovably" | Every reporting turn emits a structured JSONL trace (question, spec, tools, rows, anomalies, latency). Actions are recorded with status + timestamps. |
| **Information disclosure** | Over-broad data exposure | Read tools return only aggregates/escalations; `validate_spec` rejects off-schema fields; result `limit` is capped. Destination numbers are masked in the data. |
| **Denial of service** | Expensive/unbounded queries | `limit` is hard-capped (≤1000) by `validate_spec`; rules carry `min_sample`/`window`; MCP is read-only and cheap. |
| **Elevation of privilege** | LLM output triggering an action | Structurally impossible: there is no action tool. Prompt injection can at worst yield a wrong *report*, never a state change. |

## 4. Controls and where they live

- **Input guardrail (reporting):** `validate_spec` rejects any spec referencing a
  field, metric, value, or limit outside the schema. One self-repair retry, then refuse.
- **Input guardrail (skills):** `validate_rule` rejects malformed anomaly rules
  before they can run.
- **Anti-fabrication:** the narrator prompt constrains the model to use only the
  numbers it is given; detection/arithmetic is deterministic code.
- **Least privilege:** `CDRBackend` connects with `read_only=True`; the MCP server
  exposes read tools only (`get_schema`, `run_report`, `list_escalations`,
  `get_route_detail`, `get_window_summary`) — no mutation tools.
- **Human-in-the-loop:** block/dismiss is portal-only; blocked routes are recorded
  and suppressed on later ticks.
- **Deterministic detection:** monitoring reasoning comes from skills/rules, not
  the model, and is verified by `tests/test_monitor.py` — not subject to model whims.
- **Secrets:** `GEMINI_API_KEY` and any MCP API key are read from environment /
  platform secrets, never committed. See conventions below.
- **Observability:** `agent/trace.py` writes one structured line per turn for audit.

## 5. The paved road (pre-approved secure conventions)

Contributors stay on these defaults so security is the path of least resistance:

1. New data access goes through `AggregationSpec` + `validate_spec`. No raw SQL
   string-building, ever; never interpolate user input into a query.
2. New agent capabilities are **read tools** unless a capability genuinely needs
   to act — and any action capability must route through a human approval step.
3. Secrets come from environment variables only. No keys in code, config, or git.
   `.gitignore` excludes env files; data artifacts (`*.duckdb`, `*.parquet`) are
   regenerated, never committed.
4. Every model output that will be shown as fact must be grounded in returned
   data; narration may not introduce numbers.
5. Detection thresholds live in rules/skills (reviewable), not in prompts.
6. Changes pass `make security` (Semgrep) and the test + judge suites before merge.

## 6. Shift-left tooling

- **Semgrep** runs as a pre-commit hook and a `make security` target
  (`p/python`, `p/security-audit`), catching injection, unsafe calls, and secrets
  patterns locally before commit.
- **Pre-commit hooks** also block accidental secret commits and large/binary files.
- **LLM-as-judge** evals grade reporting output faithfulness and trajectory
  validity each run; deterministic tests verify the monitoring skills.

## 7. Residual risks (honest)

- Synthetic data and a replayed stream: production would add authn/z on the portal,
  rate limiting, and a real CDR ingest path — the agent/security boundaries are
  already shaped for that.
- Prompt injection can degrade *report quality*; it cannot cause an action, by design.
- The portal in the demo has no auth; a real deployment must put the block action
  behind authenticated, authorised operators.
