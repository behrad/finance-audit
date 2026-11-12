from datetime import date

from app import jobs
from app.skills import SkillRegistry
from app.store import Store

DAY = date(2026, 11, 6)


def calls(http):
    return [(l["method"], l["path"].split("/accounts/demo_account")[1]) for l in http.get("/mock-payhawk/__log").json()]


def run(client, s):
    jobs.fetch(client, s, run_date=DAY)
    return jobs.analyze(client, s, run_date=DAY)


def test_fetch_is_raw_then_analyze_writes_back_and_is_idempotent(client, http):
    s = Store()
    f = jobs.fetch(client, s, run_date=DAY)
    assert f["received"] == 14 and not s.reviews and calls(http) == []  # Feed only: no LLM, no write-back
    assert jobs.analyze(client, s, run_date=DAY)["analyzed"] == len(s.reviews) > 0
    ex = lambda i: client.expense(i)
    assert ex("1002")["status"] == "ready"                       # auto-approved under $10
    assert ex("1009")["supplier"]["name"] == "Hana Kato"         # supplier fixed on a draft
    assert ex("1010")["category"]["name"] == "Travel"            # category fixed on a draft
    assert ex("1001")["status"] == "draft"                       # clean but >= $10: left for finance
    n = len(http.get("/mock-payhawk/__log").json())
    assert jobs.pending(s, SkillRegistry().get()) == []
    assert jobs.analyze(client, s, run_date=DAY)["analyzed"] == 0
    again = jobs.fetch(client, s, run_date=DAY)                  # our patches changed 1009/1010; 1002 is ready -> left the queue
    assert again["updated"] == 2 and again["new"] == 0 and "1002" not in {i["expense_id"] for i in s.poll_logs[-1]["items"]}
    assert jobs.analyze(client, s, run_date=DAY)["analyzed"] == 2
    assert len(http.get("/mock-payhawk/__log").json()) == n      # nothing written twice (comments hashed, fixes done)


def test_poll_limit_does_not_orphan_backlog(client):
    s = Store(poll_limit=2)
    r = jobs.fetch(client, s, run_date=DAY)
    assert r["new"] == 2 and r["truncated"] and len(s.feed_sigs) == 2
    assert jobs.fetch(client, s, run_date=DAY)["new"] == 2 and len(s.feed_sigs) == 4  # next 2, not the same 2
    s.poll_limit = None
    assert jobs.fetch(client, s, run_date=DAY)["new"] == 10 and not s.fetch_truncated
    assert len(s.expenses) == 14


def test_analyze_only_touches_pending(client):
    s = Store(poll_limit=2)
    jobs.fetch(client, s, run_date=DAY)
    first = set(s.feed_sigs)
    assert jobs.analyze(client, s, run_date=DAY)["analyzed"] <= 2 and set(s.reviews) <= first
    jobs.fetch(client, s, run_date=DAY)
    assert set(jobs.pending(s, SkillRegistry().get())) >= set(s.feed_sigs) - first - {i for i in s.feed_sigs if not s.expenses[i]["document"]["files"]}


def test_ask_review_comments_and_survives_rereview(client, http):
    s = Store()
    run(client, s)
    jobs.set_action(client, s, "1004", "ask_review", "please explain the amount")
    assert "review requested by finance. please explain the amount" in client.comments("1004")[-1]["payload"]
    http.post("/mock-payhawk/__edit/1004", json={"note": "edited"})
    run(client, s)
    r = s.reviews["1004"]
    assert r["action"] == "ask_review" and r["comment"] == "please explain the amount"  # human action + comment kept (P8)
    assert client.expense("1004")["status"] == "draft"


def test_repeat_approve_is_idempotent(client, http):
    s = Store()
    run(client, s)
    jobs.set_action(client, s, "1001", "approve")
    assert client.expense("1001")["status"] == "ready"
    n = len(s.log)
    r = jobs.set_action(client, s, "1001", "approve")             # repeat: no error, no second call
    assert len(s.log) == n
    assert r["action_done"] == "approve"


def test_approve_comment_lands_and_dry_run_ask_stays_pending(client):
    from app.writeback import Switches
    s = Store()
    run(client, s)
    jobs.set_action(client, s, "1001", "approve", "looks fine")
    assert "approved by finance. looks fine" in client.comments("1001")[-1]["payload"]
    r = jobs.set_action(client, s, "1004", "ask_review", "why?", cfg=Switches(dry_run=True))
    assert r["action_done"] == "" and not any("why?" in c["payload"] for c in client.comments("1004"))
    assert jobs.decide(client, s, Switches(dry_run=False))["applied"] == 1      # sent once writes are on
    assert "why?" in client.comments("1004")[-1]["payload"]


def test_analyze_log_records_each_llm_input(client):
    s = Store()
    jobs.fetch(client, s, run_date=DAY)
    jobs.analyze(client, s, run_date=DAY)
    steps = [c for i in s.poll_logs[-1]["items"] for c in i["llm"]]
    assert {c["step"] for c in steps} == {"extract", "rules"} and all(c["request"] for c in steps)


def test_fetch_reads_only_the_review_queue(client, http):
    from app.payhawk.mock import store as mock
    mock.expense("1003")["approvalProcess"]["status"] = "pending"   # still with the approver
    client.set_status("1004", "ready")                              # already reviewed
    s = Store()
    jobs.fetch(client, s, run_date=DAY)
    assert "1003" not in s.expenses and "1004" not in s.expenses and len(s.expenses) == 12
