# InsightDesk — SMS Traffic Intelligence

An agentic system over A2P SMS call-detail records (CDR). Two surfaces:

- **Reporting agent** — ask about traffic in plain English ("delivery rate by
  operator", "profit by vendor"); it builds a validated query, runs it, flags
  anomalies, and answers with a chart.
- **Ambient monitoring agent** — watches CDR traffic as it arrives, evaluates
  enterprise rules per route, and raises **escalations** to a portal where a
  human can **block** or **dismiss** a bad route.

Built for the Google × Kaggle 5-Day AI Agents capstone (Agents for Business).

## Core principle

The model does language; deterministic code does data. The model turns a
question into a query spec and a rule-instruction into a rule — it never writes
SQL, never decides what counts as an anomaly, and never invents a number.

## Quickstart

```bash
pip install -r requirements.txt
python -m insightdesk.data.cdr_seed            # generate CDR data + scenarios

# offline checks (no key):
python -m insightdesk.tests.test_monitor       # ambient agent on the stream
python -m insightdesk.monitor.simulate         # watch escalations stream live
python -m insightdesk.tests.test_skills
python -m insightdesk.eval.run_eval --mock

# live (your Gemini key):
export GEMINI_API_KEY=...
python -m insightdesk.run_agent "delivery rate by operator"
uvicorn insightdesk.web.app:app                # http://localhost:8000
```

## The dataset (CDR)

One row per message: `cdr_id, created_ts, delivery_ts, client_id, vendor_id,
country, operator, sender, content_type (promotional|transactional),
delivery_status, rate, cost, profit, latency`. Metrics: message_count, cost,
profit, rate, delivery_rate (avg of delivered), latency. Four planted scenarios:
route degradation, fraud surge, margin leak, OTP failure.

## Ambient operation

The monitoring agent runs as a real server-side loop. Click **Auto** in the
portal (or POST `/monitor/start`, or boot with `INSIGHTDESK_AUTORUN=1`) and the
server advances the traffic stream and re-evaluates the rules on a timer —
escalations accumulate even with no browser open. **Feed next batch** steps one
batch manually; **Block route** is the human-approved action; **Reset** restarts
the stream.

## Monitoring rules (skills)

Named business rules evaluated per route window: `route_degradation` (delivery
< 65%), `otp_failure` (transactional delivery < 80%, critical), `traffic_surge`
(volume >> baseline), `margin_leak` (loss-making route). Each carries a severity
and a recommended action. New rules can be defined in plain English and compiled.

## Course concepts (≥3 required; this applies six)

- **Multi-agent (Day 1)** — separate reporting and monitoring agents.
- **Tools (Day 2)** — schema/query tools; the human block step is a
  long-running operation with approval.
- **Memory (Day 3)** — multi-turn reporting follow-ups.
- **Skills (Day 3)** — anomaly rules as named, instruction-definable skills.
- **Quality & guardrails + evals (Day 4)** — spec/rule validation; eval harness.
- **Ambient + production (Day 5)** — event-driven monitoring, tracing, deployed.

## Layout

```
insightdesk/
  data/cdr_seed.py             CDR generator + planted scenarios
  backends/cdr_backend.py      reporting aggregations (spec -> SQL)
  agent/                       llm, prompts, spec_agent, skills, anomaly,
                               orchestrator, trace  (reporting agent)
  monitor/                     rules, escalation, engine, stream  (ambient agent)
  web/app.py + static/         reporting chat + monitoring portal
  eval/                        spec-accuracy eval set + runner
  tests/                       test_monitor, test_skills
  run_agent.py                 reporting CLI
```

## Skills (SKILL.md format)

Skills follow the SKILL.md folder structure and are loaded at runtime, not
hardcoded:

```
skills/
  anomaly-detection/     SKILL.md + references/rule_types.md + assets/builtin_skills.json
  traffic-monitoring/    SKILL.md + assets/rules.json
```

`agent/skill_loader.py` reads each skill's `SKILL.md` (name/description) and its
`assets/*.json` definitions. `agent/skills.py::BUILTIN_SKILLS` and
`monitor/rules.py::RULES` are populated from those folders. Editing a skill's
JSON asset changes behavior with no code change. Evaluators stay in code
(deterministic); the model only compiles a new rule from plain English, once.

## MCP server (read-only)

The system exposes its tools over the Model Context Protocol so any MCP client
(Claude, Gemini CLI, an ADK agent) can read traffic state — `get_schema`,
`run_report`, `list_escalations`, `get_route_detail`, `get_window_summary`. By
design there are **no action tools**: blocking/dismissing a route is always a
human action in the portal, which bounds the agent to reads.

```bash
make mcp          # run the stdio MCP server
```

## Evaluation (LLM-as-judge)

`eval/judge.py` grades the reporting agent on two metrics — **output
faithfulness** (answer uses only returned numbers) and **trajectory validity**
(spec on-schema and on-target). The monitoring agent's reasoning is deterministic
(skills) and is verified by `tests/test_monitor.py`, not judged.

```bash
make judge        # offline mock
make eval         # spec-accuracy eval
```

## Security

See `SECURITY.md` for the STRIDE threat model and the "paved road" of secure
conventions. Highlights: the model never takes a consequential action (no action
tools; block is human-only), read-only data access, two input guardrails
(`validate_spec`, `validate_rule`), deterministic detection, and structured
tracing. Shift-left tooling: Semgrep pre-commit hooks and a `make security` scan.

```bash
make hooks        # install pre-commit (Semgrep + secret/hygiene checks)
make security     # run the Semgrep security scan
```
