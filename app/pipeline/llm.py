"""LLM steps. LLM_MODE=replay (default) reads recorded answers from fixtures/llm/, so the demo needs no keys;
LLM_MODE=live runs the ADK agents against Gemini (GOOGLE_API_KEY or Vertex env)."""
import asyncio
import contextvars
import time
import json
import os
from pathlib import Path

from app.pipeline.models import ReceiptExtraction, Verdict
from app.skills import Skill

REPLAY = Path(__file__).resolve().parents[2] / "fixtures" / "llm"
# Set from Settings (app/web.py): {"model": "openai/gpt-4o", "api_key": "..."}. Overrides the skill's Gemini model via LiteLLM.
# shortcut: in-memory only, lost on restart (never persist a secret to disk here); move to a secret store before multi-tenant.
override: dict | None = None
# jobs.analyze points this at a per-expense list; each LLM step appends {step, model, called_llm, ok, ms, response|error} for the Logs page.
sink: contextvars.ContextVar[list | None] = contextvars.ContextVar("llm_sink", default=None)
EXTRACT_PROMPT = ("Read this receipt and extract its fields. Dates as YYYY-MM-DD. total is the amount actually paid. "
                  "legibility is 0..1: how confidently you could read it.")


def _model(skill: Skill):
    if not override:
        return skill.model
    from google.adk.models.lite_llm import LiteLlm
    return LiteLlm(model=override["model"], api_key=override["api_key"])


def build_extract_agent(skill: Skill):
    from google.adk.agents import LlmAgent
    return LlmAgent(name="extract", model=_model(skill), instruction=EXTRACT_PROMPT,
                    output_schema=ReceiptExtraction, include_contents="none")


def build_rules_agent(skill: Skill):
    """The only place where a tenant changes agent behaviour (SPEC §5). Callable instruction avoids ADK's {var} templating."""
    from google.adk.agents import LlmAgent
    text = f"{skill.body}\n\nAllowed categories: {', '.join(skill.categories)}\nAllowed flags: {', '.join(skill.flags)}"
    return LlmAgent(name="rules", model=_model(skill), instruction=lambda _ctx: text,
                    output_schema=Verdict, include_contents="none")


def _run(agent, parts) -> str:
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    async def go():
        runner = InMemoryRunner(agent=agent, app_name="receipt-auditor")
        s = await runner.session_service.create_session(app_name="receipt-auditor", user_id="svc")
        async for ev in runner.run_async(user_id="svc", session_id=s.id,
                                         new_message=types.Content(role="user", parts=parts)):
            if ev.is_final_response() and ev.content:
                return ev.content.parts[0].text
        raise RuntimeError("agent returned no final response")

    return asyncio.run(go())  # callers are sync (threadpool / jobs); no running loop here


def _step(step: str, skill: Skill, live: bool, produce, parse):
    """Run produce() -> raw text -> parse(); record what happened (even on failure) into the sink."""
    rec = {"step": step, "model": override["model"] if override else skill.model, "called_llm": live}
    t = time.perf_counter()
    try:
        rec["response"] = produce()
        return parse(rec["response"])
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {str(e)[:600]}"
        raise
    finally:
        rec["ok"], rec["ms"] = "error" not in rec, round((time.perf_counter() - t) * 1000)
        if (out := sink.get()) is not None:
            out.append(rec)


def extract(skill: Skill, file_id: str, data: bytes, mime: str) -> ReceiptExtraction:
    live = os.getenv("LLM_MODE", "replay") != "replay"

    def produce():
        if not live:
            return (REPLAY / "extract" / f"{file_id}.json").read_text()
        from google.genai import types
        return _run(build_extract_agent(skill), [types.Part.from_bytes(data=data, mime_type=mime), types.Part(text=EXTRACT_PROMPT)])

    return _step("extract", skill, live, produce, ReceiptExtraction.model_validate_json)


def judge(skill: Skill, expense: dict, rx: ReceiptExtraction, facts: list) -> Verdict:
    live = os.getenv("LLM_MODE", "replay") != "replay"

    def produce():
        if not live:
            return (REPLAY / "rules" / f"{expense['id']}.json").read_text()
        from google.genai import types
        payload = {"expense": {k: expense.get(k) for k in ("title", "note", "category", "supplier", "owner", "reconciliation")},
                   "receipt": rx.model_dump(), "check_results": [f.__dict__ for f in facts]}
        return _run(build_rules_agent(skill), [types.Part(text=json.dumps(payload, default=str))])

    return _step("rules", skill, live, produce, Verdict.model_validate_json)
