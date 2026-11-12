"""extract (LLM) -> facts (Python) -> rules (LLM) -> Review record. Plain function orchestration, no ADK Workflow:
the only agent steps are the two LLM calls in llm.py."""
import re
from datetime import UTC, date, datetime

from app.pipeline import llm
from app.pipeline.checks import CHECKS, Ctx
from app.pipeline.models import Fact
from app.skills import Skill

CODE = re.compile(r"[a-z][a-z0-9_]{1,48}")  # shape of a flag code the rules LLM may invent


def review_expense(e: dict, skill: Skill, ctx: Ctx, client) -> dict:
    f = e["document"]["files"][0]  # shortcut: only the first receipt file is read; extend when multi-file expenses appear
    rx = llm.extract(skill, f["fileId"], client.download(f), f["contentType"])

    facts: list[Fact] = []
    unreadable = False
    for name, params in skill.checks.items():
        if name in CHECKS and (fact := CHECKS[name](e, rx, ctx, params)):
            fact.flag = params.get("flag") or fact.flag  # code as named in the Rules
            facts.append(fact)
            if name == "readable":
                unreadable = True
                break  # nothing else can be trusted

    errors, fixes, flags = [], {}, {x.flag: x.detail for x in facts}
    if not unreadable:
        verdict = llm.judge(skill, e, rx, facts)
        for sf in verdict.flags:
            if sf.name in skill.flags or CODE.fullmatch(sf.name):  # new codes allowed: a user rule may define its own
                flags.setdefault(sf.name, sf.description)
            else:
                errors.append(f"model returned unknown flag '{sf.name}'")
        if verdict.category_fix in skill.categories and verdict.category_fix != e["category"]["name"]:
            fixes["category"] = verdict.category_fix
    for x in facts:
        fixes.update(x.fix)

    clean = not flags and not errors
    action, source = "", ""
    cap = skill.checks.get("auto_approve_under")
    if clean and cap and e["reconciliation"]["baseTotalAmount"] < cap["amount"]:
        action, source = "approve", f"auto_under_{cap['amount']:g}"

    return {
        "expense_id": e["id"], "status": "clean" if clean else "needs attention",
        "flags": list(flags), "category_for_attention": next(iter(flags), ""),
        "description": "; ".join(errors + list(flags.values())),
        "flag_details": [{"flag": "error", "detail": m} for m in errors] + [{"flag": k, "detail": v} for k, v in flags.items()],
        "fixes": fixes, "action": action, "action_source": source,
        "duplicate_of": next((x.duplicate_of for x in facts if x.duplicate_of), ""),
        "receipt": rx.model_dump(), "skill_version": skill.version, "model": skill.model,
        "processed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def review_all(client, skill: Skill, run_date: date | None = None, ctx: Ctx | None = None) -> list[dict]:
    ctx = ctx or Ctx(run_date or date.today())
    expenses = sorted(client.expenses(), key=lambda e: (e["createdAt"], e["id"]))
    return [review_expense(e, skill, ctx, client) for e in expenses]
