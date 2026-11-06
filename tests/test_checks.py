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
