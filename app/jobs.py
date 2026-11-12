"""fetch: Payhawk -> Feed (raw). analyze: Feed -> review -> save -> write back. decide: apply pending manual actions.
Local: `python -m app.jobs fetch|analyze|decide`; Cloud Run Jobs use the same entry point."""
import sys
import time
from datetime import UTC, date, datetime, timedelta

from app.pipeline import llm
from app.payhawk.client import PayhawkClient
from app.pipeline.run import review_expense
from app.skills import SkillRegistry
from app.store import Store, is_manual
from app.writeback import Switches, apply


def _sig(e: dict) -> tuple:
    return (e.get("modifiedAt", ""), tuple(f["fileId"] for f in e.get("document", {}).get("files", [])))


def _files(e: dict) -> list:
    return e.get("document", {}).get("files", [])


def fetch(client: PayhawkClient, store: Store, run_date: date | None = None) -> dict:
    """Payhawk -> Feed only: raw data, no LLM, no write-back."""
    t0 = time.perf_counter()
    run_date = run_date or date.today()
    store.ctx.run_date = run_date
    # date filter ignores time of day (R4): re-fetch a 2-day window; full backfill first and after a truncated fetch (else the older backlog is orphaned)
    flt = {"status": {"$equal": "draft"}}  # Review tab = approved but not yet reviewed (status still draft)
    if store.feed_sigs and not store.fetch_truncated:
        flt["modifiedAt"] = {"$greaterThan": (run_date - timedelta(days=2)).isoformat()}
    # approvalProcess.status has no server-side filter, so drop not-submitted / pending / rejected here
    received = sorted((e for e in client.expenses(flt) if (e.get("approvalProcess") or {}).get("status") == "approved"),
                      key=lambda e: (e["createdAt"], e["id"]))
    fresh = [e for e in received if _sig(e) != store.feed_sigs.get(e["id"])]
    taken = {e["id"]: e for e in fresh[: store.poll_limit]}  # poll_limit caps how much lands per fetch (None = all)
    store.fetch_truncated = len(fresh) > len(taken)
    new = updated = 0
    items = []
    for e in received:
        if e["id"] in taken:
            st = "updated" if e["id"] in store.feed_sigs else "new"
            new, updated = new + (st == "new"), updated + (st == "updated")
            store.expenses[e["id"]], store.feed_sigs[e["id"]] = e, _sig(e)
        else:
            st = "skipped: over limit" if _sig(e) != store.feed_sigs.get(e["id"]) else "unchanged"
        items.append({"expense_id": e["id"], "title": e.get("title"), "payhawk_status": e.get("status"), "state": st, "llm": []})
    run = _record(store, "fetch", t0, received=len(received), new=new, updated=updated, truncated=store.fetch_truncated)
    store.poll_logs = (store.poll_logs + [{"kind": "fetch", "at": run["at"], "received": len(received), "items": items}])[-10:]
    return run


def pending(store: Store, skill) -> list[str]:
    """Feed ids with a receipt that were never analyzed, changed since, or were analyzed under other Rules."""
    ids = [i for i, e in store.expenses.items() if _files(e) and i in store.feed_sigs and (
        i not in store.reviews or store.sigs.get(i) != store.feed_sigs[i] or store.reviews[i]["skill_version"] != skill.version)]
    return sorted(ids, key=lambda i: (store.expenses[i]["createdAt"], i))


def analyze(client: PayhawkClient, store: Store, tenant="default", run_date: date | None = None, cfg: Switches | None = None) -> dict:
    t0 = time.perf_counter()
    run_date, cfg, skill = run_date or date.today(), cfg or Switches(), SkillRegistry().get(tenant)
    store.ctx.run_date = run_date
    todo = pending(store, skill)
    reviewed = failed = 0
    items = []
    for eid in todo:
        e, sig = store.expenses[eid], store.feed_sigs[eid]
        it = {"expense_id": eid, "title": e.get("title"), "payhawk_status": e.get("status"), "state": "reviewed", "llm": []}
        items.append(it)
        tok = llm.sink.set(it["llm"])
        try:
            r = review_expense(e, skill, store.ctx, client)
        except Exception as ex:  # one bad receipt / model outage must not abort the run; it stays pending (sig not saved)
            failed += 1
            err = f"{type(ex).__name__}: {str(ex)[:300]}"
            it["state"], it["error"], store.failed[eid] = "failed", err, err
            store.log.append({"expense_id": eid, "call": "review_failed", "error": err[:150]})
            continue
        finally:
            llm.sink.reset(tok)
        it["result"] = {k: r[k] for k in ("status", "flags", "description", "fixes", "action")}
        old = store.reviews.get(eid)
        if old and is_manual(old["action_source"]):  # P8: a human's action is never overwritten by the service
            r["action"], r["action_source"] = old["action"], old["action_source"]
        if old:
            r["changes_applied"], r["action_done"], r["comment"] = old.get("changes_applied", ""), old.get("action_done", ""), old.get("comment", "")
        store.reviews[eid], store.sigs[eid] = r, sig
        store.failed.pop(eid, None)
        apply(client, store, r, cfg)
        reviewed += 1
    run = _record(store, "analyze", t0, analyzed=reviewed, failed=failed)
    store.poll_logs = (store.poll_logs + [{"kind": "analyze", "at": run["at"], "received": len(todo), "items": items}])[-10:]
    return run


def decide(client: PayhawkClient, store: Store, cfg: Switches | None = None) -> dict:
    t0, done = time.perf_counter(), 0
    for r in store.reviews.values():
        if r["action"] in ("approve", "ask_review") and r.get("action_done") != r["action"]:
            apply(client, store, r, cfg or Switches())
            done += 1
    return _record(store, "decide", t0, applied=done)


def set_action(client, store: Store, eid: str, action: str, comment="", source="dashboard", cfg: Switches | None = None) -> dict:
    r = store.reviews[eid]
    r["comment"] = comment
    r["action"], r["action_source"], r["action_done"] = action, source if action else "", ""
    apply(client, store, r, cfg or Switches())
    return r


def _record(store: Store, kind: str, t0: float, **counts) -> dict:
    run = {"kind": kind, "at": datetime.now(UTC).isoformat(timespec="seconds"), "duration_s": round(time.perf_counter() - t0, 2), **counts}
    store.runs.append(run)
    return run


if __name__ == "__main__":
    s, c = Store(), PayhawkClient()
    cmd = sys.argv[1:]
    if cmd == ["analyze"]:  # fresh in-process Store: feed first, since state is in-memory
        fetch(c, s)
    print({"fetch": fetch, "analyze": analyze}.get(cmd[0] if cmd else "", decide)(c, s))
