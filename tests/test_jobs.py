from datetime import date

from app import jobs
from app.store import Store

DAY = date(2026, 11, 6)


def calls(http):
    return [(l["method"], l["path"].split("/accounts/demo_account")[1]) for l in http.get("/mock-payhawk/__log").json()]


def test_poll_writes_back_and_is_idempotent(client, http):
    s = Store()
    assert jobs.poll(client, s, run_date=DAY)["reviewed"] == 14
    ex = lambda i: client.expense(i)
    assert ex("1002")["status"] == "ready"                       # auto-approved under $10
    assert ex("1009")["supplier"]["name"] == "Hana Kato"         # supplier fixed on a draft
    assert ex("1010")["category"]["name"] == "Travel"            # category fixed on a draft
    assert ex("1001")["status"] == "draft"                       # clean but >= $10: left for finance
    n = len(http.get("/mock-payhawk/__log").json())
    again = jobs.poll(client, s, run_date=DAY)
    assert again["reviewed"] == 3 and again["seen"] == 3         # only our own patched expenses re-reviewed
    assert len(http.get("/mock-payhawk/__log").json()) == n      # nothing written twice (comments hashed, fixes done)


def test_manual_reject_comments_and_survives_rereview(client, http):
    s = Store()
    jobs.poll(client, s, run_date=DAY)
    jobs.set_action(client, s, "1004", "reject")
    assert "rejected by finance" in client.comments("1004")[-1]["payload"]
    http.post("/mock-payhawk/__edit/1004", json={"note": "edited"})
    jobs.poll(client, s, run_date=DAY)
    assert s.reviews["1004"]["action"] == "reject"                # human action kept (P8)
    assert client.expense("1004")["status"] == "draft"


def test_repeat_approve_is_idempotent(client, http):
    s = Store()
    jobs.poll(client, s, run_date=DAY)
    jobs.set_action(client, s, "1001", "approve")
    assert client.expense("1001")["status"] == "ready"
    n = len(s.log)
    r = jobs.set_action(client, s, "1001", "approve")             # repeat: no error, no second call
    assert len(s.log) == n
    assert r["action_done"] == "approve"
