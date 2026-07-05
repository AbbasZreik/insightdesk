"""
Loads skills from the SKILL.md folder format under insightdesk/skills/.

Each skill is a folder with a SKILL.md (YAML frontmatter: name, description),
optional references/, and assets/ holding the machine-readable definitions:
  skills/anomaly-detection/assets/builtin_skills.json  -> reporting anomaly skills
  skills/traffic-monitoring/assets/rules.json          -> monitoring rules

This is what makes the project genuinely follow the SKILL.md structure: the skill
definitions are DATA loaded from skill folders at import time, not hardcoded.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def skill_metadata(skill_name: str, skills_dir: Path = SKILLS_DIR) -> dict[str, Any]:
    """Parse the YAML frontmatter (name, description) from a skill's SKILL.md."""
    md = (skills_dir / skill_name / "SKILL.md").read_text(encoding="utf-8")
    if md.lstrip().startswith("---"):
        body = md.split("---", 2)
        if len(body) >= 3:
            try:
                import yaml
                return yaml.safe_load(body[1]) or {}
            except Exception:
                pass
    return {}


def load_anomaly_skill_defs(skills_dir: Path = SKILLS_DIR) -> dict[str, dict]:
    """Return {name: {description, rule}} from the anomaly-detection skill."""
    path = skills_dir / "anomaly-detection" / "assets" / "builtin_skills.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_monitor_rule_defs(skills_dir: Path = SKILLS_DIR) -> list[dict]:
    """Return the list of monitoring-rule dicts from the traffic-monitoring skill."""
    path = skills_dir / "traffic-monitoring" / "assets" / "rules.json"
    return json.loads(path.read_text(encoding="utf-8"))
