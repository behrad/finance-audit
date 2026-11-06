"""Deterministic checks. Each: (expense, extraction, ctx, params) -> Fact | None. Add one here and name it in a SKILL.md."""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.pipeline.models import Fact, ReceiptExtraction


@dataclass
class Ctx:
    run_date: date
    seen: dict = field(default_factory=dict)  # receipt fingerprint -> {expense_id: createdAt}, whole tenant history


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.casefold())


def readable(e, rx, ctx, p):
    if rx.legibility < p["min_legibility"]:
        return Fact("unreadable_receipt", f"Receipt is not legible enough to check (score {rx.legibility:.2f}).")


def duplicate(e, rx, ctx, p):
    fp = (_norm(rx.merchant), _norm(rx.receipt_number), round(rx.total, 2), rx.currency, rx.date)
    seen = ctx.seen.setdefault(fp, {})
    seen[e["id"]] = e["createdAt"]
    original = min(seen.items(), key=lambda kv: (kv[1], kv[0]))[0]  # earliest createdAt is the original
    if original != e["id"]:
        return Fact("duplicate", f"Same receipt as expense {original}.", duplicate_of=original)


def total_matches_receipt(e, rx, ctx, p):
    r = e["reconciliation"]
    if r["currency"] != rx.currency or abs(r["totalAmount"] - rx.total) > 0.005:
        return Fact("total_mismatch", f"Expense total {r['totalAmount']:.2f} {r['currency']} but receipt shows {rx.total:.2f} {rx.currency}.")


def value_matches(e, rx, ctx, p):
    r = e["reconciliation"]
    expected = rx.total * r["fxRate"]
    if abs(r["baseTotalAmount"] - expected) > p["tolerance"]:
        return Fact("wrong_value", f"Amount in {r['baseCurrency']} is {r['baseTotalAmount']:.2f} but the receipt gives {expected:.2f}.")


def fx_deviation(e, rx, ctx, p):
    r = e["reconciliation"]
    ref = p.get("reference_rates", {}).get(r["currency"])
    if r["currency"] == r["baseCurrency"] or ref is None:  # shortcut: no reference rate for a currency -> not judged
        return None
    dev = round(abs(r["fxRate"] / ref - 1), 6)
    if dev > p["tolerance"]:
        return Fact("wrong_exchange_rate", f"Rate {r['fxRate']} deviates {dev:.1%} from reference {ref} (tolerance {p['tolerance']:.0%}).")


def supplier_is_employee(e, rx, ctx, p):
    o = e["owner"]
    name = f"{o['firstName']} {o['lastName']}"
    if _norm((e.get("supplier") or {}).get("name", "")) != _norm(name):
        return Fact("supplier_not_employee", f"Supplier should be the claimant ({name}).", fix={"supplier": name})


def document_date_matches(e, rx, ctx, p):
    d = e["document"]["documentDate"][:10]
    if d != rx.date:
        return Fact("date_mismatch", f"Document date is {d} but the receipt is dated {rx.date}.")


def number_matches(e, rx, ctx, p):
    n = e["document"].get("documentNumber", "")
    if _norm(n) != _norm(rx.receipt_number):
        return Fact("number_mismatch", f"Document number is {n} but the receipt says {rx.receipt_number}.")


def service_period(e, rx, ctx, p):
    am = e.get("amortization") or {}
    start, end = am.get("startDate") or rx.service_start, am.get("endDate") or rx.service_end  # Q2 default
    if not end:
        return None
    first_of_month = ctx.run_date.replace(day=1)
    if _d(end) < first_of_month or _d(end) > ctx.run_date + timedelta(days=p["max_future_days"]):
        return Fact("service_period_out_of_range", f"Service period {start[:10]} to {end[:10]} is outside the current month ({first_of_month:%Y-%m}).")


CHECKS = {f.__name__: f for f in (readable, duplicate, total_matches_receipt, value_matches, fx_deviation,
                                  supplier_is_employee, document_date_matches, number_matches, service_period)}
