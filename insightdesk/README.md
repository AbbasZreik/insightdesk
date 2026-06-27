# InsightDesk — natural-language business analytics agent

A capstone-ready agent (Agents for Business track) that turns plain-English
questions into validated aggregation queries over a messaging/billing dataset,
flags anomalies, and returns a narrated answer plus a chart.

This repository is a **clean, synthetic-data demonstration** of an agent pattern
— it deliberately ships no proprietary code or real customer data.

## Why two backends

The agent only ever produces a backend-agnostic `AggregationSpec`. Two adapters
implement the same contract:

| Backend | Role | Why |
|---|---|---|
| **DuckDB** (`backends/duckdb_backend.py`) | Public live demo | Single file, zero services — runs on a free tier and never goes down during judging. |
| **Elasticsearch** (`backends/es_backend.py`) | Production showcase | Demonstrates real aggregation-DSL engineering; swap in with one line. |

Swapping is a single construction change; the agent code is identical:

```python
from insightdesk.backends.duckdb_backend import DuckDBBackend
backend = DuckDBBackend("insightdesk/data/insightdesk.duckdb")
# or, for the production path:
# from insightdesk.backends.es_backend import ESBackend
# backend = ESBackend("http://localhost:9200")
```

## Quickstart

```bash
pip install -r requirements.txt
python -m insightdesk.data.seed --out insightdesk/data --days 180 --seed 42

# offline pipeline test — no API key, no network, no quota:
python -m insightdesk.tests.test_pipeline

# live agent on YOUR Gemini key (independent of Antigravity's quota):
export GEMINI_API_KEY=...        # from Google AI Studio
python -m insightdesk.run_agent "What is the monthly cost trend for Europe?"
python -m insightdesk.run_agent  # interactive REPL, remembers context

# spec-accuracy evals (offline mock, or real model):
python -m insightdesk.eval.run_eval --mock
python -m insightdesk.eval.run_eval            # uses your key

# web demo (the live project link):
uvicorn insightdesk.web.app:app --reload       # http://localhost:8000
INSIGHTDESK_MOCK=1 uvicorn insightdesk.web.app:app   # offline UI test, no key
```

That writes `events.parquet`, `clients.parquet`, and a ready-to-query
`insightdesk.duckdb` with three reproducible planted anomalies:

1. **Usage spike** — Falcon Media (C001) SMS volume ×8 for one week.
2. **Revenue dip** — Europe billed cost −40% for one month.
3. **Churn** — Summit Logistics (C008) goes silent after day 120.

Edit the `ANOMALIES` dict in `data/seed.py` to control exactly what the agent
should catch.

## How a question flows

```
question
  -> [spec_agent] Gemini emits a JSON AggregationSpec, grounded in the schema
  -> [validate_spec] reject anything off-schema (one self-repair retry)
  -> [backend] DuckDB / Elasticsearch runs the same spec
  -> [anomaly] deterministic modified-z (time) / IQR (groups) flags outliers
  -> [narrator] Gemini writes a short answer using ONLY the returned numbers
```

The model never writes SQL and never invents numbers; it only produces a spec
and narrates verified results.

## Project layout

```
insightdesk/
  data/seed.py                 synthetic data + planted anomalies
  backends/base.py             AggregationSpec, SchemaInfo, validate_spec(), ABC
  backends/duckdb_backend.py   demo adapter (spec -> SQL)
  backends/es_backend.py       production adapter (spec -> ES composite agg)
  agent/llm.py                 LLM interface, GeminiLLM (your key), MockLLM
  agent/prompts.py             schema-aware spec + grounded-narration prompts
  agent/spec_agent.py          NL -> validated spec, with self-repair retry
  agent/anomaly.py             deterministic anomaly detection
  agent/orchestrator.py        InsightAgent: ties it together + session memory
  agent/trace.py               structured JSONL tracing (observability)
  eval/cases.py                question -> expected-spec eval set
  eval/run_eval.py             eval runner (spec accuracy per category)
  web/app.py                   FastAPI: /ask endpoint + chat UI (the live demo)
  web/static/index.html        Chart.js frontend; anomalies highlighted amber
  tests/test_pipeline.py       full offline pipeline test (MockLLM)
  run_agent.py                 CLI (single question or REPL)
  requirements.txt
```

## Course concepts demonstrated (capstone requires ≥3)

- **Tools / MCP (Day 2)** — `get_schema()` and `run_aggregation()` are the agent's
  two tools; expose them over MCP.
- **Sessions & memory (Day 3)** — multi-turn follow-ups ("now break that down by
  region") reuse prior context.
- **Quality & guardrails (Day 4)** — `validate_spec()` rejects any out-of-schema
  field, metric, value, or limit before a query runs; add an eval set of
  question → expected-spec pairs.
- **Prototype → production (Day 5)** — tool-call logging + the dual-backend design
  + the deployed live link.

The anomaly-detection layer (z-score / IQR over aggregation results, narrated by
the agent) is the differentiator on top of the required concepts.

## Status

- [x] Synthetic data generator with planted anomalies
- [x] Backend contract + guardrail validation
- [x] DuckDB adapter (tested)
- [x] Elasticsearch adapter (skeleton — smoke-test against live ES 8.x)
- [x] Agent layer: NL -> validated spec, self-repair, session memory
- [x] Anomaly detector (robust modified-z / IQR) + grounded narrator
- [x] CLI (single question + REPL)
- [x] Offline end-to-end test (MockLLM, no key)
- [x] Eval set + runner (spec accuracy per category)
- [x] Structured tracing / observability
- [x] FastAPI + Chart.js web demo
- [ ] Deploy the live link (Hugging Face Spaces / Render)

## Deploy the live link

Standard FastAPI service. Start command:
`uvicorn insightdesk.web.app:app --host 0.0.0.0 --port $PORT`, set `GEMINI_API_KEY`
as a secret, and ship the embedded `insightdesk.duckdb`. Because the demo backend
is a single file, there is no database service to keep alive — the link stays up
through judging.
