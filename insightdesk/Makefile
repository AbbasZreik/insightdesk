# InsightDesk developer + security targets.
# Most targets run offline; `judge` and `serve` use your GEMINI_API_KEY.

.PHONY: help seed test monitor skills eval judge traces security hooks mcp serve

help:
	@echo "seed     - generate the CDR dataset + planted scenarios"
	@echo "test     - deterministic monitor + skills tests"
	@echo "eval     - reporting spec-accuracy eval (mock)"
	@echo "judge    - LLM-as-judge: output faithfulness + trajectory validity (mock)"
	@echo "traces   - write judged traces to eval/traces.jsonl (mock)"
	@echo "security - Semgrep security scan (p/python, p/security-audit)"
	@echo "hooks    - install pre-commit hooks"
	@echo "mcp      - run the read-only SMS MCP server (stdio)"
	@echo "serve    - run the web app (reporting + monitoring portal)"

seed:
	python -m insightdesk.data.cdr_seed

test:
	python -m insightdesk.tests.test_monitor
	python -m insightdesk.tests.test_skills

eval:
	python -m insightdesk.eval.run_eval --mock

judge:
	python -m insightdesk.eval.judge --mock

traces:
	python -m insightdesk.eval.judge --mock --out insightdesk/eval/traces.jsonl

security:
	semgrep --config p/python --config p/security-audit insightdesk/ --error

hooks:
	pip install pre-commit && pre-commit install

mcp:
	python -m insightdesk.mcp_server.server

serve:
	uvicorn insightdesk.web.app:app --reload
