"""日程建構的邊界行為，特別是資料不足時必須明確失敗而非悄悄截短契約。"""

from __future__ import annotations

import pandas as pd
import pytest

from fcn.backtest import run_backtest
from fcn.schedule import build_schedule
from fcn.terms import FCNTerms, KIType, KOType
from tests.test_backtest import synthetic_md

SYMS = ["AAA", "BBB", "CCC"]


def terms(**kw) -> FCNTerms:
    d = dict(
        underlyings=SYMS,
        tenor_months=12,
        coupon_pa=0.12,
        strike_pct=0.80,
        ko_type=KOType.DAILY_MEMORY,
        autocall_pct=1.00,
        ki_type=KIType.AKI,
        ki_pct=0.60,
    )
    d.update(kw)
    return FCNTerms(**d)


def test_raises_when_data_ends_before_final_valuation():
    """契約尚未到期時必須報錯，不可把期末評價日夾到最後一個交易日。"""
    days = pd.bdate_range("2024-01-02", periods=200, name="date")   # 約 9.5 個月
    with pytest.raises(ValueError, match="不足以涵蓋期末評價日"):
        build_schedule(terms(), days, days[0])


def test_schedule_ok_when_data_just_covers_tenor():
    days = pd.bdate_range("2024-01-02", periods=300, name="date")
    sch = build_schedule(terms(), days, days[0])
    target = sch.trade_date + pd.DateOffset(months=12)
    assert sch.final_valuation <= target
    assert (target - sch.final_valuation).days <= 4      # 僅為順延至交易日的差距


def test_backtest_excludes_contracts_that_have_not_matured():
    """回測不得納入尚未走完契約期間的進場日。"""
    md = synthetic_md(n_days=900)
    bt = run_backtest(terms(tenor_months=6), md, step_days=10)

    last = md.trading_days[-1]
    assert bt.outcomes, "應至少有一筆完整契約"
    for o in bt.outcomes:
        assert o.schedule.final_valuation <= last
        # 每一筆都必須是「足月」契約，而非被截短的
        target = o.schedule.trade_date + pd.DateOffset(months=6)
        assert (target - o.schedule.final_valuation).days <= 4

    # 最後一個進場日之後，剩餘資料必定不足一個完整契約期間
    latest = max(o.schedule.trade_date for o in bt.outcomes)
    assert latest + pd.DateOffset(months=6) <= last


def test_backtest_start_filter_is_respected():
    md = synthetic_md(n_days=900)
    start = md.trading_days[400]
    bt = run_backtest(terms(tenor_months=6), md, start=start, step_days=10)
    assert all(o.schedule.trade_date >= start for o in bt.outcomes)
