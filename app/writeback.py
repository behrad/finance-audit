"""Write-back to Payhawk (SPEC §7). PATCH only works on drafts; approve = mark `ready`; reject = comment."""
import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.store import Store


@dataclass
class Switches:
    patch: bool = True
    mark_ready: bool = True
    comment: bool = True
    dry_run: bool = field(default_factory=lambda: os.getenv("WRITEBACK_DRY_RUN") == "1")  # P10: set in .env for live; unset = writes


def _call(store: Store, cfg: Switches, eid: str, what: str, fn, *args, **kw):
    store.log.append({"ts": datetime.now(UTC).isoformat(timespec="seconds"), "expense_id": eid, "call": what,
                      "args": kw or list(args), "dry_run": cfg.dry_run})
    return None if cfg.dry_run else fn(*args, **kw)


def _comment_once(client, store, cfg, eid, text):
    h = hashlib.sha1(text.encode()).hexdigest()
    seen = store.comment_hashes.setdefault(eid, set())
    if h not in seen:
        _call(store, cfg, eid, "comment", client.comment, eid, text)
        if not cfg.dry_run:
            seen.add(h)


def _resolve(client, name: str, items: list[dict], create=None) -> str:
    hit = next((i for i in items if i["name"].casefold() == name.casefold()), None)
    return (hit or create(name))["id"]  # Q6 default: create a missing supplier


def apply(client, store: Store, review: dict, cfg: Switches) -> None:
    """Bring Payhawk in line with one review. Idempotent: safe to call on every poll/decide."""
    eid = review["expense_id"]
    e = client.expense(eid)
    draft = e["status"] == "draft"
    fixes = review["fixes"]

    if fixes and draft and cfg.patch and not review.get("changes_applied"):
        body = {}
        if "supplier" in fixes:
            body["supplier"] = {"id": _resolve(client, fixes["supplier"], client.suppliers(), client.create_supplier)}
        if "category" in fixes:
            body["category"] = {"id": _resolve(client, fixes["category"], client.categories())}
        _call(store, cfg, eid, "patch", client.patch_expense, eid, **body)
        if not cfg.dry_run:
            review["changes_applied"] = "; ".join(f"{k} -> {v}" for k, v in fixes.items())

    action = review["action"]
    if action == "approve" and review.get("action_done") != "approve":
        if draft and cfg.mark_ready:
            _call(store, cfg, eid, "status", client.set_status, eid, "ready")
        if e["status"] == "ready" or (draft and cfg.mark_ready and not cfg.dry_run):  # already reviewed counts as done
            review["action_done"] = "approve"
    elif action == "reject" and cfg.comment:
        _comment_once(client, store, cfg, eid, f"Receipt Auditor: rejected by finance. {review['description']}".strip())
        review["action_done"] = "reject"
    elif not action and review["status"] == "needs attention" and cfg.comment:
        _comment_once(client, store, cfg, eid, f"Receipt Auditor: needs attention. {review['description']}")
