---
name: anomaly-detection
description: >
  Detect anomalies in tabular query results using named, reusable rules. A skill
  is a plain-language description plus a machine-checkable rule; the model may
  author a rule from an instruction, but detection is always deterministic.
  Used by the reporting agent to flag drops, spikes, underperformers, and
  outliers, and to compile new anomaly definitions from plain English.
---

# Anomaly Detection

A skill is a named detector: a description plus a `rule`. Built-in skills live in
`assets/builtin_skills.json`; the rule-type catalog is in `references/rule_types.md`.

## How the project loads this
`agent/skill_loader.py` reads `assets/builtin_skills.json` at import time to
populate `agent/skills.py::BUILTIN_SKILLS`. The evaluator `apply_skill` and the
`compile_skill_from_instruction` (one model call, then validated) live in
`agent/skills.py`. Detection is deterministic; the model never decides per-row.
