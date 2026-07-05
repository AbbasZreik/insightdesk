"""
Thin async runner service so FastAPI (or anything) can call an ADK agent.

Builds the agent + Runner + session service once; ask() runs one turn and
returns the final text. Live calls need a Gemini key:
    GOOGLE_GENAI_USE_VERTEXAI=FALSE
    GOOGLE_API_KEY=<AI Studio key>
"""
from __future__ import annotations

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

APP = "insightdesk"


class ADKService:
    def __init__(self, agent, app_name: str = APP):
        self.app = app_name
        self.sessions = InMemorySessionService()
        self.runner = Runner(agent=agent, app_name=app_name,
                             session_service=self.sessions)

    async def ask(self, question: str, user_id: str = "u",
                  session_id: str = "default") -> dict:
        """Run one turn. Returns {"answer": text, "report": <run_report output or None>}
        so the caller can still render a chart from the captured tool result."""
        try:
            await self.sessions.create_session(
                app_name=self.app, user_id=user_id, session_id=session_id)
        except Exception:
            pass  # already exists
        content = types.Content(role="user", parts=[types.Part(text=question)])
        final, report = "", None
        async for ev in self.runner.run_async(
                user_id=user_id, session_id=session_id, new_message=content):
            try:
                for fr in ev.get_function_responses():
                    if fr.name == "run_report" and isinstance(fr.response, dict):
                        report = fr.response
            except Exception:
                pass
            if ev.is_final_response() and ev.content and ev.content.parts:
                final = ev.content.parts[0].text or final
        return {"answer": final, "report": report}
