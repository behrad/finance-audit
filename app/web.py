import asyncio
import csv
import io
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app import jobs
from app.pipeline import llm
from app.payhawk.client import PayhawkApiError, PayhawkClient
from app.payhawk.offline import OfflineClient
from app.payhawk.mock import router as mock_payhawk
from app.rows import COLUMNS, row
from app.skills import SkillRegistry
from app.store import Store
from app import writeback
from app.writeback import Switches


def poll_interval() -> int:
    return int(os.getenv("POLL_INTERVAL_SECONDS") or 0)  # 0 = manual only


@asynccontextmanager
async def lifespan(_app):
    async def loop():
        while True:
            await asyncio.sleep(poll_interval())
            if not store.auto_poll:
                continue
            try:
                await asyncio.to_thread(jobs.fetch, make_client(), store)  # fetch only, never analyze
            except Exception as e:  # keep the loop alive; the failure is visible in the run log
                store.runs.append({"kind": "fetch", "error": str(e)[:200]})
    task = asyncio.create_task(loop()) if poll_interval() > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Receipt Auditor", lifespan=lifespan)
app.include_router(mock_payhawk)
store = Store()


data_mode: str | None = None  # set from Settings; in-memory, falls back to DATA_MODE env (default live)


def make_client() -> PayhawkClient:  # tests override this
    return OfflineClient() if (data_mode or os.getenv("DATA_MODE", "live")) == "offline" else PayhawkClient()


def originals() -> set:
    return {r["duplicate_of"] for r in store.reviews.values() if r.get("duplicate_of")}


def rows() -> list[dict]:
    o = originals()
    return [row(store.expenses[i], r, o) for i, r in store.reviews.items()]


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def dashboard():
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")


def _mode(c) -> str:
    return "OFFLINE" if isinstance(c, OfflineClient) else "MOCK" if "mock-payhawk" in c.base_url else "LIVE"


@app.get("/api/info")
def info():
    return {"mode": _mode(make_client()), "today": date.today().isoformat()}


def _settings() -> dict:
    c = make_client()
    url, off = c.base_url, isinstance(c, OfflineClient)
    mock = "mock-payhawk" in url
    m = re.search(r"/accounts/([^/]+)", url)
    skill = SkillRegistry().get()
    mode = os.getenv("LLM_MODE", "replay")
    last = lambda k: next((r for r in reversed(store.runs) if r["kind"] == k and "duration_s" in r), None)
    return {
        "mode": _mode(c),
        "data_source": f"Offline files: {c.root} ({c.count()} expenses)" if off else "Built-in mock (fixtures)" if mock else "Payhawk API",
        "payhawk_host": re.sub(r"^https?://([^/]+).*", r"\1", url), "account_id": m.group(1) if m else None,
        "last_fetch": last("fetch"), "last_analyze": last("analyze"), "poll_interval_s": poll_interval(),
        "auto_poll": store.auto_poll, "poll_limit": store.poll_limit, "pending": len(jobs.pending(store, skill)),
        "llm": {"mode": mode, "provider": "Custom (LiteLLM)" if llm.override else "Google (Gemini)" if skill.model.startswith("gemini") else "other",
                "model": llm.override["model"] if llm.override else skill.model, "custom": bool(llm.override), "calls_model": mode == "live"},
        "writeback_dry_run": Switches().dry_run,
    }


@app.get("/api/settings")
def settings():
    return _settings()


class LlmOverride(BaseModel):
    model: str   # LiteLLM name, e.g. "openai/gpt-4o"
    api_key: str


@app.put("/api/settings/llm")
def set_llm(body: LlmOverride):
    model, key = body.model.strip(), body.api_key.strip()
    if not model or not key:
        raise HTTPException(400, "model and api_key are required")
    llm.override = {"model": model, "api_key": key}  # the key is never returned by any endpoint
    return _settings()["llm"]


@app.delete("/api/settings/llm")
def clear_llm():
    llm.override = None
    return _settings()["llm"]


class PollCfg(BaseModel):
    auto_poll: bool | None = None
    poll_limit: int | None = None


@app.put("/api/settings/poll")
def set_poll(body: PollCfg):
    given = body.model_fields_set
    if "poll_limit" in given and body.poll_limit is not None and body.poll_limit < 1:
        raise HTTPException(400, "poll_limit must be >= 1 or null")
    if "poll_limit" in given:
        store.poll_limit = body.poll_limit
    if body.auto_poll is not None:
        store.auto_poll = body.auto_poll
    return _settings()


class DryRun(BaseModel):
    dry_run: bool


@app.put("/api/settings/writeback")
def set_writeback(body: DryRun):
    writeback.dry_run_override = body.dry_run
    return _settings()


class Mode(BaseModel):
    mode: Literal["offline", "live"]


@app.put("/api/settings/mode")
def set_mode(body: Mode):
    global data_mode
    data_mode = body.mode
    return _settings()


@app.post("/api/settings/test-connection")
def test_connection():
    """Real read against the configured Payhawk (or mock) to prove where data comes from."""
    t = time.perf_counter()
    try:
        a = make_client().account()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "account_id": a.get("id"), "account_name": a.get("name"), "latency_ms": round((time.perf_counter() - t) * 1000)}


def live_client() -> PayhawkClient:  # always the real API, never offline; tests override this
    return PayhawkClient()


@app.get("/api/payhawk/expenses/{eid}")
def payhawk_expense(eid: str):
    try:
        c = live_client()
        return {**c.expense(eid), "comments": c.comments(eid)}  # GET /expenses has no comments; they live at /comments
    except Exception as e:  # PayhawkApiError, network
        raise HTTPException(502, str(e))


@app.get("/api/reviews")
def reviews():
    return rows()


@app.get("/api/reviews/{eid}")
def review(eid: str):
    if eid not in store.reviews:
        raise HTTPException(404, "unknown expense")
    return {"row": row(store.expenses[eid], store.reviews[eid], originals()), "review": store.reviews[eid],
            "files": _item(eid)["files"], "raw": store.expenses[eid]}


class Action(BaseModel):
    action: str  # approve | ask_review | "" (clear)
    comment: str = ""


@app.post("/api/reviews/{eid}/action")
def action(eid: str, body: Action):
    if eid not in store.reviews or body.action not in ("approve", "ask_review", ""):
        raise HTTPException(400, "bad expense or action")
    n = len(store.log)
    try:
        r = jobs.set_action(make_client(), store, eid, body.action, body.comment)
    except Exception as e:  # PayhawkApiError, network: say so instead of a bare 500
        raise HTTPException(502, f"Payhawk write failed: {str(e)[:200]}")
    return {**r, "writes": store.log[n:]}  # what this Save sent (or only logged, if dry run)


@app.post("/api/runs/fetch")
def run_fetch():
    return jobs.fetch(make_client(), store)


@app.post("/api/runs/analyze")
def run_analyze():
    return jobs.analyze(make_client(), store)


@app.post("/api/runs/decide")
def run_decide():
    return jobs.decide(make_client(), store)


def _item(eid: str) -> dict:
    e = store.expenses[eid]
    o, rec, doc, r = e.get("owner") or {}, e.get("reconciliation") or {}, e.get("document") or {}, store.reviews.get(eid)
    files = doc.get("files", [])
    state = ("no receipt" if not files else "failed" if eid in store.failed else "new" if not r else
             "changed" if store.sigs.get(eid) != store.feed_sigs.get(eid) else
             "rules changed" if r["skill_version"] != SkillRegistry().get().version else "analyzed")
    return {"expense_id": eid, "title": e.get("title", ""), "employee": f"{o.get('firstName', '')} {o.get('lastName', '')}".strip(),
            "supplier": (e.get("supplier") or {}).get("name", ""), "category": (e.get("category") or {}).get("name", ""),
            "note": e.get("note", ""), "amount": rec.get("totalAmount", ""), "currency": rec.get("currency", ""),
            "payhawk_status": e.get("status", ""), "document_date": (doc.get("documentDate") or "")[:10], "created_at": e.get("createdAt", ""),
            "files": [{"url": f"/api/receipts/{eid}/{f['fileId']}", "content_type": f.get("contentType", ""), "name": f.get("name", "")} for f in files],
            "state": state, "error": store.failed.get(eid, "")}


@app.get("/api/feed")
def feed():
    return [_item(i) for i in sorted(store.expenses, key=lambda i: (store.expenses[i].get("createdAt", ""), i), reverse=True)]


@app.get("/api/feed/{eid}")
def feed_item(eid: str):
    if eid not in store.expenses:
        raise HTTPException(404, "unknown expense")
    return {"item": _item(eid), "raw": store.expenses[eid]}


class Rules(BaseModel):
    text: str


def _rules() -> dict:
    reg = SkillRegistry()
    return {"text": reg.text(), "default": reg.default_text(), "custom": reg.custom()}


@app.get("/api/rules")
def get_rules():
    return _rules()


@app.put("/api/rules")
def put_rules(body: Rules):
    try:
        SkillRegistry().save("default", body.text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _rules()


@app.delete("/api/rules")
def reset_rules():
    SkillRegistry().reset()
    return _rules()


@app.get("/api/logs")
def logs():
    return store.poll_logs[::-1]  # newest first


@app.get("/api/runs")
def runs():
    return {"runs": store.runs[-20:], "writeback_log": store.log[-50:]}


@app.get("/api/export.csv")
def export_csv():
    out = io.StringIO()
    w = csv.DictWriter(out, COLUMNS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows())
    return Response(out.getvalue(), media_type="text/csv")


_receipts: dict[str, bytes] = {}


@app.get("/api/receipts/{eid}/{fid}")
def receipt(eid: str, fid: str):
    f = next((f for f in store.expenses.get(eid, {}).get("document", {}).get("files", []) if f["fileId"] == fid), None)
    if not f:
        raise HTTPException(404, "unknown receipt")
    if fid not in _receipts:  # shortcut: unbounded in-memory cache, bound it before large tenants
        _receipts[fid] = make_client().download(f)
    return Response(_receipts[fid], media_type=f["contentType"])
