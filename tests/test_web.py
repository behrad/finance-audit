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
