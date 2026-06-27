"""
LLM abstraction for InsightDesk.

Why an interface instead of calling Gemini directly everywhere:
  - The whole pipeline (spec generation + narration) becomes testable offline
    with MockLLM — no API key, no network, no quota — which is how the test
    suite proves the agent works before you ever plug your key in.
  - Swapping models (Flash vs Pro) or even providers is a one-line change.

GeminiLLM uses your OWN Google AI Studio key, so it draws on your personal
Gemini API quota and is completely independent of Antigravity's built-in
agent quota.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Callable

# Set to whatever Flash-tier model your AI Studio account currently lists.
# Flash is the right default here: cheap, fast, plenty for spec + narration.
DEFAULT_MODEL = os.environ.get("INSIGHTDESK_MODEL", "gemini-2.5-flash")


class LLM(ABC):
    @abstractmethod
    def generate_json(self, system: str, user: str) -> dict:
        """Return a parsed JSON object from the model."""

    @abstractmethod
    def generate_text(self, system: str, user: str) -> str:
        """Return free-form text from the model."""


class GeminiLLM(LLM):
    """Real backend, using the current unified `google-genai` SDK.

    Install:  pip install google-genai
    Auth:     export GEMINI_API_KEY=...   (from Google AI Studio)
    """

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        from google import genai  # imported lazily so the module loads without it
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "No Gemini API key. Set GEMINI_API_KEY or pass api_key=..."
            )
        self._genai = genai
        self.client = genai.Client(api_key=key)
        self.model = model

    def generate_json(self, system: str, user: str) -> dict:
        from google.genai import types
        resp = self.client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                temperature=0,            # deterministic spec generation
            ),
        )
        return json.loads(resp.text)

    def generate_text(self, system: str, user: str) -> str:
        from google.genai import types
        resp = self.client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.2,
            ),
        )
        return resp.text.strip()


class MockLLM(LLM):
    """Offline stand-in for tests and demos.

    Pass either:
      - a dict mapping the lowercased user prompt substring -> response, or
      - a callable (system, user) -> response (dict for json, str for text).
    """

    def __init__(self, json_responses: dict | Callable | None = None,
                 text_responses: dict | Callable | None = None):
        self.json_responses = json_responses or {}
        self.text_responses = text_responses or {}

    def _resolve(self, table, system, user, default):
        if callable(table):
            return table(system, user)
        low = user.lower()
        for key, val in table.items():
            if key.lower() in low:
                return val
        return default

    def generate_json(self, system: str, user: str) -> dict:
        return self._resolve(self.json_responses, system, user, {})

    def generate_text(self, system: str, user: str) -> str:
        return self._resolve(self.text_responses, system, user,
                             "(mock narration)")
