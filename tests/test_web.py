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
    assert api.post("/api/runs/fetch").json()["received"] == 14
    assert api.get("/api/reviews").json() == [] and len(api.get("/api/feed").json()) == 14
    assert api.post("/api/runs/analyze").json()["analyzed"] > 0
    rows = api.get("/api/reviews").json()
    assert rows and rows[0]["employee"]
    assert api.post("/api/reviews/1004/action", json={"action": "ask_review", "comment": "why?"}).json()["action_done"] == "ask_review"
    assert api.post("/api/reviews/1004/action", json={"action": "reject"}).status_code == 400
    assert api.post("/api/reviews/1004/action", json={"action": "bogus"}).status_code == 400
    assert api.get("/api/receipts/1001/f-1001").content[:4] == b"\x89PNG"
    assert len(list(csv.DictReader(io.StringIO(api.get("/api/export.csv").text)))) == len(rows)
    assert api.get("/api/info").json()["mode"] == "MOCK"


def test_settings(api, monkeypatch):
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "300")
    api.post("/api/runs/fetch")
    s = api.get("/api/settings").json()
    assert s["mode"] == "MOCK" and s["poll_interval_s"] == 300 and s["last_fetch"]["duration_s"] >= 0 and s["pending"] > 0
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
    api.post("/api/runs/fetch")
    assert api.get("/api/logs").json()[0]["kind"] == "fetch"
    api.post("/api/runs/analyze")
    log = api.get("/api/logs").json()[0]
    assert log["kind"] == "analyze" and log["received"] > 0
    it = next(i for i in log["items"] if i["state"] == "reviewed")
    assert {c["step"] for c in it["llm"]} <= {"extract", "rules"} and it["llm"][0]["ok"] and it["llm"][0]["called_llm"] is False


def test_feed_items_and_receipt_cache(api):
    api.post("/api/runs/fetch")
    f = api.get("/api/feed").json()
    assert {i["state"] for i in f} <= {"new", "no receipt"} and f[0]["created_at"] >= f[-1]["created_at"]
    one = next(i for i in f if i["files"])
    d = api.get(f"/api/feed/{one['expense_id']}").json()
    assert d["item"] == one and d["raw"]["id"] == one["expense_id"] and api.get("/api/feed/nope").status_code == 404
    assert api.get(one["files"][0]["url"]).content == api.get(one["files"][0]["url"]).content


def test_rules_edit_flips_feed_state(api):
    api.post("/api/runs/fetch")
    api.post("/api/runs/analyze")
    assert "rules changed" not in {i["state"] for i in api.get("/api/feed").json()}
    r = api.get("/api/rules").json()
    assert r["custom"] is False and r["text"] == r["default"]
    assert api.put("/api/rules", json={"text": r["text"] + "\nExtra rule."}).json()["custom"] is True
    assert "rules changed" in {i["state"] for i in api.get("/api/feed").json()}
    assert api.get("/api/settings").json()["pending"] > 0
    assert api.delete("/api/rules").json()["custom"] is False
    assert "rules changed" not in {i["state"] for i in api.get("/api/feed").json()}


def test_invalid_rules(api):
    for bad in ("no frontmatter", "---\nname: [\n---\nbody", "---\nname: x\n---\nbody"):
        r = api.put("/api/rules", json={"text": bad})
        assert r.status_code == 400 and r.json()["detail"]
    assert api.get("/api/rules").json()["custom"] is False


def test_poll_settings(api):
    assert api.put("/api/settings/poll", json={"auto_poll": False}).json()["auto_poll"] is False
    s = api.put("/api/settings/poll", json={"poll_limit": 2}).json()
    assert s["poll_limit"] == 2 and s["auto_poll"] is False
    assert api.put("/api/settings/poll", json={"poll_limit": None}).json()["poll_limit"] is None
    assert api.put("/api/settings/poll", json={"poll_limit": 0}).status_code == 400


def test_offline_mode(tmp_path, monkeypatch, http):
    from app.payhawk.offline import OfflineClient
    (tmp_path / "7.json").write_text('{"id": "7", "document": {"files": [{"fileId": "f7", "contentType": "application/pdf"}]}}')
    (tmp_path / "7").mkdir()
    (tmp_path / "7" / "f7.pdf").write_bytes(b"%PDF")
    c = OfflineClient(tmp_path)
    assert [e["id"] for e in c.expenses()] == ["7"] and c.expense("7")["id"] == "7"
    assert c.download(c.files("7")[0]) == b"%PDF" and c.comment("7", "x") == {}
    monkeypatch.setattr(web, "data_mode", None)
    monkeypatch.setenv("OFFLINE_DIR", str(tmp_path))
    assert http.put("/api/settings/mode", json={"mode": "offline"}).json()["mode"] == "OFFLINE"
    assert isinstance(web.make_client(), OfflineClient) and http.get("/api/info").json()["mode"] == "OFFLINE"
    assert http.put("/api/settings/mode", json={"mode": "live"}).json()["mode"] != "OFFLINE"
    assert http.put("/api/settings/mode", json={"mode": "x"}).status_code == 422


def test_multi_flag_details(client, monkeypatch):
    from datetime import date

    from app.pipeline import run
    from app.pipeline.models import SemanticFlag, Verdict
    from app.rows import row
    from app.skills import SkillRegistry
    skill = SkillRegistry().get("default")
    monkeypatch.setattr(run.llm, "judge", lambda *a: Verdict(flags=[SemanticFlag(name=next(f for f in skill.flags if f != "fx_difference"), description="llm says so")]))
    e = client.expense("1005")
    r = run.review_expense(e, skill, run.Ctx(date(2026, 11, 6)), client)
    assert len(r["flags"]) > 1 and [d["flag"] for d in r["flag_details"]] == r["flags"]
    assert row(e, r)["flag_details"] == r["flag_details"]


def test_payhawk_live_fetch_and_review_raw(api, monkeypatch):
    api.post("/api/runs/fetch"); api.post("/api/runs/analyze")
    eid = api.get("/api/reviews").json()[0]["expense_id"]
    d = api.get(f"/api/reviews/{eid}").json()
    assert d["raw"]["id"] == eid and isinstance(d["files"], list)

    class Live:
        def expense(self, i):
            if i == "bad":
                raise web.PayhawkApiError(404, "boom")
            return {"id": i, "live": True}

        def comments(self, i):
            return [{"payload": "hi"}]
    monkeypatch.setattr(web, "live_client", lambda: Live())
    assert api.get("/api/payhawk/expenses/x1").json() == {"id": "x1", "live": True, "comments": [{"payload": "hi"}]}
    r = api.get("/api/payhawk/expenses/bad")
    assert r.status_code == 502 and "boom" in r.json()["detail"]
