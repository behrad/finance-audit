"""poll: fetch -> skip unchanged -> review -> save -> write back. decide: apply pending manual actions.
Local: `python -m app.jobs poll|decide`; Cloud Run Jobs use the same entry point."""
import sys
from datetime import date, timedelta

from app.payhawk.client import PayhawkClient
from app.pipeline.run import review_expense
from app.skills import SkillRegistry
from app.store import Store, is_manual
from app.writeback import Switches, apply


def poll(client: PayhawkClient, store: Store, tenant="default", run_date: date | None = None, cfg: Switches | None = None) -> dict:
    run_date, cfg, skill = run_date or date.today(), cfg or Switches(), SkillRegistry().get(tenant)
    store.ctx.run_date = run_date
    # date filter ignores time of day (R4): re-fetch a 2-day window; first run is a full backfill
    flt = {"modifiedAt": {"$greaterThan": (run_date - timedelta(days=2)).isoformat()}} if store.runs else None
    seen = reviewed = 0
    for e in sorted(client.expenses(flt), key=lambda e: (e["createdAt"], e["id"])):
        seen += 1
        sig = (e["modifiedAt"], tuple(f["fileId"] for f in e["document"]["files"]))
        if store.sigs.get(e["id"]) == sig:
            continue
        r = review_expense(e, skill, store.ctx, client)
        old = store.reviews.get(e["id"])
        if old and is_manual(old["action_source"]):  # P8: a human's action is never overwritten by the service
            r["action"], r["action_source"] = old["action"], old["action_source"]
        if old:
            r["changes_applied"], r["action_done"] = old.get("changes_applied", ""), old.get("action_done", "")
        store.reviews[e["id"]], store.expenses[e["id"]], store.sigs[e["id"]] = r, e, sig
        apply(client, store, r, cfg)
        reviewed += 1
    return _record(store, "poll", seen=seen, reviewed=reviewed)


def decide(client: PayhawkClient, store: Store, cfg: Switches | None = None) -> dict:
    done = 0
    for r in store.reviews.values():
        if r["action"] in ("approve", "reject") and r.get("action_done") != r["action"]:
            apply(client, store, r, cfg or Switches())
            done += 1
    return _record(store, "decide", applied=done)


def set_action(client, store: Store, eid: str, action: str, source="dashboard", cfg: Switches | None = None) -> dict:
    r = store.reviews[eid]
    r["action"], r["action_source"], r["action_done"] = action, source if action else "", ""
    apply(client, store, r, cfg or Switches())
    return r


def _record(store: Store, kind: str, **counts) -> dict:
    run = {"kind": kind, "at": date.today().isoformat(), **counts}
    store.runs.append(run)
    return run


if __name__ == "__main__":
    s, c = Store(), PayhawkClient()
    print(poll(c, s) if sys.argv[1:] == ["poll"] else decide(c, s))
