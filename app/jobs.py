"""poll: fetch -> skip unchanged -> review -> save -> write back. decide: apply pending manual actions.
Local: `python -m app.jobs poll|decide`; Cloud Run Jobs use the same entry point."""
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta

from app.payhawk.client import PayhawkClient
from app.pipeline.run import review_expense
from app.skills import SkillRegistry
from app.store import Store, is_manual
from app.writeback import Switches, apply


def poll(client: PayhawkClient, store: Store, tenant="default", run_date: date | None = None, cfg: Switches | None = None) -> dict:
    t0 = time.perf_counter()
    run_date, cfg, skill = run_date or date.today(), cfg or Switches(), SkillRegistry().get(tenant)
    store.ctx.run_date = run_date
    # date filter ignores time of day (R4): re-fetch a 2-day window; first run is a full backfill
    flt = {"modifiedAt": {"$greaterThan": (run_date - timedelta(days=2)).isoformat()}} if store.runs else None
    seen = reviewed = failed = 0
    todo = [e for e in client.expenses(flt) if e["document"]["files"]]  # no receipt -> nothing to review
    todo = sorted(todo, key=lambda e: (e["createdAt"], e["id"]))[: int(os.getenv("POLL_LIMIT") or 10**9)]  # POLL_LIMIT: cap LLM spend on first live runs
    for e in todo:
        seen += 1
        sig = (e["modifiedAt"], tuple(f["fileId"] for f in e["document"]["files"]))
        if store.sigs.get(e["id"]) == sig:
            continue
        try:
            r = review_expense(e, skill, store.ctx, client)
        except Exception as ex:  # one bad receipt / model outage must not abort the poll; it is retried next poll (sig not saved)
            failed += 1
            store.log.append({"expense_id": e["id"], "call": "review_failed", "error": f"{type(ex).__name__}: {str(ex)[:150]}"})
            continue
        old = store.reviews.get(e["id"])
        if old and is_manual(old["action_source"]):  # P8: a human's action is never overwritten by the service
            r["action"], r["action_source"] = old["action"], old["action_source"]
        if old:
            r["changes_applied"], r["action_done"] = old.get("changes_applied", ""), old.get("action_done", "")
        store.reviews[e["id"]], store.expenses[e["id"]], store.sigs[e["id"]] = r, e, sig
        apply(client, store, r, cfg)
        reviewed += 1
    return _record(store, "poll", t0, seen=seen, reviewed=reviewed, failed=failed)


def decide(client: PayhawkClient, store: Store, cfg: Switches | None = None) -> dict:
    t0, done = time.perf_counter(), 0
    for r in store.reviews.values():
        if r["action"] in ("approve", "reject") and r.get("action_done") != r["action"]:
            apply(client, store, r, cfg or Switches())
            done += 1
    return _record(store, "decide", t0, applied=done)


def set_action(client, store: Store, eid: str, action: str, source="dashboard", cfg: Switches | None = None) -> dict:
    r = store.reviews[eid]
    r["action"], r["action_source"], r["action_done"] = action, source if action else "", ""
    apply(client, store, r, cfg or Switches())
    return r


def _record(store: Store, kind: str, t0: float, **counts) -> dict:
    run = {"kind": kind, "at": datetime.now(UTC).isoformat(timespec="seconds"), "duration_s": round(time.perf_counter() - t0, 2), **counts}
    store.runs.append(run)
    return run


if __name__ == "__main__":
    s, c = Store(), PayhawkClient()
    print(poll(c, s) if sys.argv[1:] == ["poll"] else decide(c, s))
