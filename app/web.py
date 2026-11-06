import csv
import io
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app import jobs
from app.payhawk.client import PayhawkClient
from app.payhawk.mock import router as mock_payhawk
from app.rows import COLUMNS, row
from app.store import Store

app = FastAPI(title="Receipt Auditor")
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
