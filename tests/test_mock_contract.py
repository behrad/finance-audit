"""Every mock response (and every request body we send) is validated against Payhawk's OpenAPI spec."""
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.payhawk.client import PayhawkApiError
from app.payhawk.mock import store
from tests.conftest import BASE

SPEC = json.loads((Path(__file__).resolve().parents[1] / "contracts/payhawk-v3.json").read_text())
FMT = FormatChecker()


@FMT.checks("email")
def _email(v):  # spec quirk: IActor.email is "empty for API keys" yet has format: email
    return v == "" or "@" in v


def validate(instance, schema):
    Draft202012Validator({**schema, "components": SPEC["components"]}, format_checker=FMT).validate(instance)


def spec_op(method: str, url_path: str) -> dict:
    """Find the spec operation whose path template matches a concrete mock URL."""
    path = url_path.removeprefix("/mock-payhawk")
    for tmpl, ops in SPEC["paths"].items():
        if method.lower() in ops and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", tmpl), path):
            return ops[method.lower()]
    raise AssertionError(f"{method} {path} is not in the Payhawk spec")


def check(resp, method: str, url: str):
    op = spec_op(method, url)
    spec_resp = op["responses"].get(str(resp.status_code))
    assert spec_resp, f"{method} {url}: status {resp.status_code} not documented in spec ({resp.text[:200]})"
    schema = spec_resp.get("content", {}).get("application/json", {}).get("schema")
    if schema:
        validate(resp.json(), schema)
    else:
        assert not resp.content
    return resp


def get(http, path, **kw):
    return check(http.get(BASE + path, **kw), "GET", BASE + path)


def send(http, method, path, body):
    r = http.request(method, BASE + path, json=body)
    req_schema = spec_op(method, BASE + path).get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if req_schema and r.status_code < 400:
        validate(body, req_schema)  # the body we send must itself be valid per the spec
    return check(r, method, BASE + path)


def test_reads_match_spec(http):
    get(http, "")
    for p in ("/expenses", "/expense-categories", "/suppliers", "/users", "/custom-fields"):
        assert get(http, p).json()["total"] > 0
    for e in get(http, "/expenses").json()["items"]:  # all 14 expenses must satisfy IExpense
        eid = e["id"]
        get(http, f"/expenses/{eid}")
        files = get(http, f"/expenses/{eid}/files").json()["items"]
        get(http, f"/expenses/{eid}/files/{files[0]['id']}")
        get(http, f"/expenses/{eid}/payments")
        get(http, f"/expenses/{eid}/workflow")
        get(http, f"/comments/expense/{eid}")


def test_writes_match_spec(http):
    sup = send(http, "POST", "/suppliers", {"name": "Hana Kato"})
    assert sup.status_code == 201
    r = send(http, "PATCH", "/expenses/1009", {"supplier": {"id": sup.json()["id"]}, "note": "fixed"})
    assert r.json()["supplier"]["name"] == "Hana Kato"
    send(http, "PATCH", "/expenses/1010", {"category": {"id": "201"}})
    assert send(http, "POST", "/expenses/1009/status", {"status": "ready"}).json()["status"] == "ready"
    send(http, "POST", "/comments", {"targetType": "expense", "targetId": "1009", "payload": "check me"})
    assert send(http, "PATCH", "/expenses/1009/custom-fields/401", {"value": "clean"}).status_code == 204
    assert get(http, "/comments/expense/1009").json()["total"] == 1
    assert [l["method"] for l in store.log] == ["POST", "PATCH", "PATCH", "POST", "POST", "PATCH"]


def test_error_rules(http):
    def err(resp, status):
        assert resp.status_code == status
        validate(resp.json(), {"$ref": "#/components/schemas/IError"})

    err(http.get(BASE + "/expenses", headers={"X-Payhawk-ApiKey": ""}), 401)
    err(http.get("/mock-payhawk/api/v3/accounts/nope/expenses"), 404)
    err(http.get(BASE + "/expenses/9999"), 404)
    err(http.get(BASE + "/expenses", params={"$take": 20000}), 400)
    err(http.get(BASE + "/expenses", params={"$orderBy": '[{"modifiedAt":"asc"}]'}), 400)  # not sortable
    err(http.get(BASE + "/expenses", params={"$filter": "not json"}), 400)
    err(http.patch(BASE + "/expenses/1009", json={"supplier": {"name": "by name"}}), 400)  # IDs only
    err(http.patch(BASE + "/expenses/1009", json={"supplier": {"id": "99999"}}), 400)
    err(http.patch(BASE + "/expenses/1009", json={"bogus": 1}), 400)
    err(http.post(BASE + "/expenses/1009/status", json={"status": "archived"}), 400)
    http.post(BASE + "/expenses/1009/status", json={"status": "ready"})
    err(http.patch(BASE + "/expenses/1009", json={"note": "x"}), 400)  # PATCH only while draft
    err(http.post(BASE + "/comments", json={"targetType": "expense", "targetId": "9999", "payload": "x"}), 404)


def test_filter_paging_order(http):
    q = lambda **p: http.get(BASE + "/expenses", params=p).json()
    assert q(**{"$take": 5, "$skip": 10}) | {"items": None} == {"items": None, "total": 14}
    assert len(q(**{"$take": 5, "$skip": 10})["items"]) == 4
    flt = lambda f: [e["id"] for e in q(**{"$filter": json.dumps(f)})["items"]]
    assert flt({"id": {"$equal": "1003"}}) == ["1003"]
    # date filters are day-granular: a time of day on the boundary day changes nothing
    day = flt({"createdAt": {"$equal": "2026-11-03T23:59:59Z"}})
    assert day == flt({"createdAt": {"$equal": "2026-11-03T00:00:00Z"}}) and "1002" in day
    assert "1002" not in flt({"createdAt": {"$greaterThan": "2026-11-03T00:00:00Z"}})
    assert flt({"$or": [{"id": {"$equal": "1001"}}, {"supplier.name": {"$contains": "staples"}}]}) == ["1001", "1009"]
    desc = [e["id"] for e in q(**{"$orderBy": '[{"id":"desc"}]'})["items"]]
    assert desc[0] == "1014" and desc[-1] == "1001"


def test_simulation_controls(http):
    assert http.post("/mock-payhawk/__reset", params={"released": 2}).json()["released"] == 2
    assert http.get(BASE + "/expenses").json()["total"] == 2
    assert http.get(BASE + "/expenses/1003").status_code == 404
    arrived = http.post("/mock-payhawk/__arrive", params={"n": 3}).json()
    assert arrived["arrived"] == ["1003", "1004", "1005"]
    store.expenses[0]["modifiedAt"] = "2020-01-01T00:00:00Z"
    http.post("/mock-payhawk/__edit/1001", json={"note": "edited by employee"})
    e = http.get(BASE + "/expenses/1001").json()
    assert e["note"] == "edited by employee" and e["modifiedAt"] > "2020"
    assert http.get("/mock-payhawk/__log").json() == []  # controls are not Payhawk writes
    http.post("/mock-payhawk/__reset")
    assert http.get(BASE + "/expenses").json()["total"] == 14


def test_client_roundtrip(client):
    exp = client.expenses()
    assert len(exp) == 14
    f = client.files("1001")[0]
    assert client.download(f)[:8] == b"\x89PNG\r\n\x1a\n"  # file.url points back at the mock
    assert client.set_status("1001", "ready")["status"] == "ready"
    with pytest.raises(PayhawkApiError) as ei:
        client.patch_expense("1001", note="late")
    assert ei.value.status == 400
    assert client.account()["baseCurrency"] == "USD"
