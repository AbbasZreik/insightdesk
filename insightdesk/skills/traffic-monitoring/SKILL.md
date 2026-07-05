---
name: traffic-monitoring
description: >
  Enterprise rules the ambient monitoring agent evaluates on live SMS CDR traffic
  per route. Each rule is a named business rule with a machine-checkable condition,
  a severity, and a recommended action. Detection is deterministic; a human blocks.
---

# Traffic Monitoring Rules

The monitoring rules live in `assets/rules.json`. `agent/skill_loader.py` loads
them into `monitor/rules.py::RULES`. The engine (`monitor/engine.py`) evaluates
each rule per route window and raises escalations; blocking stays human-only.
