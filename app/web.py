import asyncio
import csv
import io
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app import jobs
from app.payhawk.client import PayhawkClient
from app.payhawk.mock import router as mock_payhawk
from app.rows import COLUMNS, row
from app.skills import SkillRegistry
from app.store import Store
from app.writeback import Switches


def poll_interval() -> int:
    return int(os.getenv("POLL_INTERVAL_SECONDS") or 0)  # 0 = manual only


@asynccontextmanager
async def lifespan(_app):
    async def loop():
        while True:
            await asyncio.sleep(poll_interval())
            try:
                await asyncio.to_thread(jobs.poll, make_client(), store)
            except Exception as e:  # keep the loop alive; the failure is visible in the run log
                store.runs.append({"kind": "poll", "error": str(e)[:200]})
    task = asyncio.create_task(loop()) if poll_interval() > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Receipt Auditor", lifespan=lifespan)
app.include_router(mock_payhawk)
store = Store()


def make_client() -> PayhawkClient:  # tests override this
    return PayhawkClient()


def rows() -> list[dict]:
    return [row(store.expenses[i], r) for i, r in store.reviews.items()]


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def dashboard():
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")


@app.get("/api/info")
def info():
    return {"mode": "MOCK" if "mock-payhawk" in make_client().base_url else "LIVE", "today": date.today().isoformat()}


def _settings() -> dict:
    url = make_client().base_url
    mock = "mock-payhawk" in url
    m = re.search(r"/accounts/([^/]+)", url)
    skill = SkillRegistry().get()
    mode = os.getenv("LLM_MODE", "replay")
    last = next((r for r in reversed(store.runs) if r["kind"] == "poll" and "duration_s" in r), None)
    return {
        "mode": "MOCK" if mock else "LIVE", "data_source": "Built-in mock (fixtures)" if mock else "Payhawk API",
        "payhawk_host": re.sub(r"^https?://([^/]+).*", r"\1", url), "account_id": m.group(1) if m else None,
        "last_poll": last, "poll_interval_s": poll_interval(),
        "llm": {"mode": mode, "provider": "Google (Gemini)" if skill.model.startswith("gemini") else "other",
                "model": skill.model, "calls_model": mode == "live"},
        "writeback_dry_run": Switches().dry_run, "poll_limit": int(os.getenv("POLL_LIMIT") or 0) or None,
    }


@app.get("/api/settings")
def settings():
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


@app.get("/api/reviews")
def reviews():
    return rows()


@app.get("/api/reviews/{eid}")
def review(eid: str):
    if eid not in store.reviews:
        raise HTTPException(404, "unknown expense")
    return {"row": row(store.expenses[eid], store.reviews[eid]), "review": store.reviews[eid]}


class Action(BaseModel):
    action: str  # approve | reject | "" (clear)


@app.post("/api/reviews/{eid}/action")
def action(eid: str, body: Action):
    if eid not in store.reviews or body.action not in ("approve", "reject", ""):
        raise HTTPException(400, "bad expense or action")
    return jobs.set_action(make_client(), store, eid, body.action)


@app.post("/api/runs/poll")
def run_poll():
    return jobs.poll(make_client(), store)


@app.post("/api/runs/decide")
def run_decide():
    return jobs.decide(make_client(), store)


@app.get("/api/runs")
def runs():
    return {"runs": store.runs[-20:], "writeback_log": store.log[-50:]}


@app.get("/api/export.csv")
def export_csv():
    out = io.StringIO()
    w = csv.DictWriter(out, COLUMNS)
    w.writeheader()
    w.writerows(rows())
    return Response(out.getvalue(), media_type="text/csv")


@app.get("/api/receipts/{eid}/{fid}")
def receipt(eid: str, fid: str):
    f = next((f for f in store.expenses.get(eid, {}).get("document", {}).get("files", []) if f["fileId"] == fid), None)
    if not f:
        raise HTTPException(404, "unknown receipt")
    return Response(make_client().download(f), media_type=f["contentType"])
