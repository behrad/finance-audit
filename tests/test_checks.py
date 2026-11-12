from datetime import date

from app.pipeline.checks import Ctx, fx_deviation, service_period, duplicate
from app.pipeline.models import ReceiptExtraction

RX = ReceiptExtraction(merchant="M", receipt_number="1", date="2026-11-01", total=100, currency="EUR", legibility=1)


def exp(fx, ccy="EUR"):
    return {"id": "1", "createdAt": "a", "reconciliation": {"currency": ccy, "baseCurrency": "USD", "fxRate": fx}}


def test_fx_tolerance_boundary():
    p = {"tolerance": 0.02, "reference_rates": {"EUR": 1.0}}
    assert fx_deviation(exp(1.02), RX, None, p) is None       # exactly 2% is allowed
    assert fx_deviation(exp(1.0201), RX, None, p).flag == "wrong_exchange_rate"
    assert fx_deviation(exp(1.5, "USD"), RX, None, p) is None  # same currency as base


def test_service_period_month_boundaries():
    ctx, p = Ctx(date(2026, 11, 15)), {"max_future_days": 30}
    am = lambda end: {"amortization": {"startDate": "2026-11-01", "endDate": end}}
    assert service_period(am("2026-11-01"), RX, ctx, p) is None      # first day of run month is in
    assert service_period(am("2026-10-31"), RX, ctx, p).flag == "service_period_out_of_range"
    assert service_period(am("2026-12-15"), RX, ctx, p) is None      # run + 30 days is in
    assert service_period(am("2026-12-16"), RX, ctx, p).flag == "service_period_out_of_range"


def test_duplicate_earliest_is_original_regardless_of_processing_order():
    ctx = Ctx(date(2026, 11, 15))
    late, early = {"id": "2", "createdAt": "2026-11-03"}, {"id": "1", "createdAt": "2026-11-02"}
    assert duplicate(late, RX, ctx, {}) is None
    assert duplicate(early, RX, ctx, {}) is None            # now the original
    assert duplicate(late, RX, ctx, {}).duplicate_of == "1"


def test_rules_prompt_injects_user_rules_and_accepts_new_codes():
    from app.pipeline import llm, run
    from app.skills import SkillRegistry
    skill = SkillRegistry().get()
    text = llm._rules_text(skill)
    assert "<user_rules>\ncheck whether there is any duplicate claims" in text and "{" not in text.split("<user_rules>")[0]
    assert run.CODE.fullmatch("fx_difference") and not run.CODE.fullmatch("Bad Code!")


def test_rules_rename_check_codes_and_reject_unknown_checks():
    import pytest
    from app.skills import SkillRegistry, parse_skill
    base = SkillRegistry().default_text()
    s = parse_skill(base.replace("  number_matches: {}", "  number_matches: {flag: invoice_number_wrong}"))
    assert "invoice_number_wrong" in s.flags  # renamed code is known without editing `flags:`
    with pytest.raises(ValueError, match="unknown check"):
        parse_skill(base.replace("  number_matches: {}", "  number_matchez: {}"))


def test_original_of_a_duplicate_is_marked():
    from app.rows import row
    e = {"id": "1", "owner": {"firstName": "A", "lastName": "B"}, "reconciliation": {"baseTotalAmount": 1, "baseCurrency": "EUR", "totalAmount": 1, "currency": "EUR"},
         "document": {"documentDate": "2026-11-01", "files": [{"fileId": "f"}]}}
    r = {"duplicate_of": "", "receipt": {"total": 1, "currency": "EUR"}}
    assert row(e, r, {"1"})["duplicate_role"] == "original" and row(e, r)["duplicate_role"] == ""
