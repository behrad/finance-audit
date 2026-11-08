import csv
import io

import pytest

from app import web
from app.store import Store


@pytest.fixture
def api(http, client, monkeypatch):
    monkeypatch.setattr(web, "make_client", lambda: client)
    monkeypatch.setattr(web, "store", Store())
    monkeypatch.setattr("app.web.jobs.date", type("D", (), {"today": staticmethod(lambda: __import__("datetime").date(2026, 11, 6))}))
    return http


def test_dashboard_flow(api):
    assert api.get("/").status_code == 200
    assert api.post("/api/runs/poll").json()["reviewed"] == 14
    rows = api.get("/api/reviews").json()
    assert len(rows) == 14 and rows[0]["employee"]
    assert api.post("/api/reviews/1004/action", json={"action": "reject"}).json()["action_done"] == "reject"
    assert api.post("/api/reviews/1004/action", json={"action": "bogus"}).status_code == 400
    assert api.get("/api/receipts/1001/f-1001").content[:4] == b"\x89PNG"
    assert len(list(csv.DictReader(io.StringIO(api.get("/api/export.csv").text)))) == 14
    assert api.get("/api/info").json()["mode"] == "MOCK"


def test_settings(api, monkeypatch):
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "300")
    api.post("/api/runs/poll")
    s = api.get("/api/settings").json()
    assert s["mode"] == "MOCK" and s["poll_interval_s"] == 300 and s["last_poll"]["duration_s"] >= 0
    assert s["llm"]["model"] and api.post("/api/settings/test-connection").json()["ok"]


def test_custom_llm(api):
    from app.pipeline import llm
    assert api.put("/api/settings/llm", json={"model": "openai/gpt-4o", "api_key": " "}).status_code == 400
    r = api.put("/api/settings/llm", json={"model": "openai/gpt-4o", "api_key": "sk-secret"})
    try:
        assert r.json()["model"] == "openai/gpt-4o" and "sk-secret" not in api.get("/api/settings").text
        assert llm._model(None).model == "openai/gpt-4o"
    finally:
        assert api.delete("/api/settings/llm").json()["custom"] is False


def test_writeback_toggle(api):
    from app import writeback
    try:
        assert api.put("/api/settings/writeback", json={"dry_run": False}).json()["writeback_dry_run"] is False
    finally:
        writeback.dry_run_override = None


def test_logs(api):
    api.post("/api/runs/poll")
    log = api.get("/api/logs").json()[0]
    assert log["received"] == 14
    it = next(i for i in log["items"] if i["state"] == "reviewed")
    assert {c["step"] for c in it["llm"]} <= {"extract", "rules"} and it["llm"][0]["ok"] and it["llm"][0]["called_llm"] is False
