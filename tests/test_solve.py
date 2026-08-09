"""詢價平台「留白欄位求解」與行銷通路費（Rebate）測試。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fcn import mc
from fcn.terms import FCNTerms, KIType, KOType

SYMS = ["NVDA", "TSM", "AMD"]
TRADE_DATE = pd.Timestamp("2026-01-05")
PATHS = 8_000


def terms(**kw) -> FCNTerms:
    # 在 market() 的參數下，此組條件的公允配息約 14.5%（已扣 3% 通路費）。
    # 夾具刻意取略低的 13%，使各欄位的搜尋區間都能跨越目標值。
    d = dict(
        underlyings=SYMS,
        tenor_months=12,
        coupon_pa=0.13,
        strike_pct=0.80,
        ko_type=KOType.DAILY_MEMORY,
        autocall_pct=1.00,
        ki_type=KIType.AKI,
        ki_pct=0.75,
        rebate=0.03,
    )
    d.update(kw)
    return FCNTerms(**d)


def market(vol: float = 0.40) -> mc.MarketParams:
    n = len(SYMS)
    return mc.MarketParams(
        symbols=SYMS,
        spot=np.full(n, 100.0),
        vol=np.full(n, vol),
        div_yield=np.zeros(n),
        corr=np.full((n, n), 0.5) + np.eye(n) * 0.5,
        rate=0.04,
        drift=np.full(n, 0.08),
    )


def solve(t: FCNTerms, field: str, **kw):
    return mc.solve(t, market(), field=field, trade_date=TRADE_DATE, n_paths=PATHS, **kw)


# --------------------------------------------------------------------------
# 平台參數範圍
# --------------------------------------------------------------------------


def test_platform_limits_accepts_example_rows():
    """詢價範例的三列條件都應在平台可受理範圍內。"""
    assert terms().check_platform_limits() == []
    assert terms(ki_pct=0.70).check_platform_limits() == []


@pytest.mark.parametrize(
    "kw,expect",
    [
        ({"tenor_months": 1}, "天期"),
        ({"tenor_months": 24}, "天期"),
        ({"strike_pct": 0.45, "ki_pct": 0.40}, "執行價"),
        ({"autocall_pct": 1.30}, "提前出場價"),
        ({"ki_pct": 0.40}, "下限價"),
        ({"rebate": 0.05}, "行銷通路費"),
        ({"underlyings": ["A", "B", "C", "D", "E"]}, None),
    ],
)
def test_platform_limits_flags_out_of_range(kw, expect):
    if expect is None:                      # 5 檔標的在建構時就會被擋下
        with pytest.raises(ValueError, match="1~4 檔"):
            terms(**kw)
        return
    v = terms(**kw).check_platform_limits()
    assert any(expect in s for s in v), v


# --------------------------------------------------------------------------
# Rebate 對定價的影響
# --------------------------------------------------------------------------


def test_rebate_lowers_fair_coupon():
    """通路費由投資人的 100% 面額中先被抽走，因此壓低公允配息率。"""
    mp = market()
    a = mc.price(terms(coupon_pa=None, rebate=0.00), mp, trade_date=TRADE_DATE, n_paths=30_000)
    b = mc.price(terms(coupon_pa=None, rebate=0.03), mp, trade_date=TRADE_DATE, n_paths=30_000)

    assert b.fair_coupon_pa < a.fair_coupon_pa
    # 未扣通路費的毛公允值與 rebate 無關
    assert a.fair_coupon_gross == pytest.approx(b.fair_coupon_gross, rel=1e-9)
    assert a.fair_coupon_pa == pytest.approx(a.fair_coupon_gross, rel=1e-9)


def test_pricing_at_fair_coupon_leaves_exactly_the_rebate_on_the_table():
    """以公允配息回代，投資人取得的現值恰為 100% - 通路費。"""
    mp = market()
    fair = mc.price(
        terms(coupon_pa=None, rebate=0.03), mp, trade_date=TRADE_DATE, n_paths=30_000
    ).fair_coupon_pa
    back = mc.price(
        terms(coupon_pa=fair, rebate=0.03), mp, trade_date=TRADE_DATE, n_paths=30_000
    )
    assert back.pv_at_quote == pytest.approx(0.97, abs=1e-6)
    assert back.issuer_margin == pytest.approx(0.0, abs=1e-6)


def test_value_gap_decomposes_into_rebate_and_issuer_margin():
    pr = mc.price(terms(coupon_pa=0.15, rebate=0.03), market(),
                  trade_date=TRADE_DATE, n_paths=30_000)
    assert pr.implied_total_fee == pytest.approx(-pr.value_gap_pct)
    assert pr.issuer_margin == pytest.approx(pr.implied_total_fee - 0.03)


# --------------------------------------------------------------------------
# 留白欄位求解
# --------------------------------------------------------------------------


def test_solve_coupon_is_closed_form_and_hits_par():
    sr = solve(terms(coupon_pa=None), "coupon_pa")
    assert sr.iterations == 0                      # 封閉解
    assert sr.terms.coupon_pa == pytest.approx(sr.value)
    assert sr.pv == pytest.approx(1.0 - sr.terms.rebate, abs=1e-9)


def test_solve_rebate_is_closed_form():
    """已知配息，反解券商還剩多少空間可以給通路費。"""
    sr = solve(terms(coupon_pa=0.10, rebate=0.0), "rebate")
    assert sr.iterations == 0
    assert sr.terms.rebate == pytest.approx(sr.value)
    # 報價 15% 遠低於這組高波動條件的公允值，故仍留有正的抽成空間
    assert sr.value > 0


@pytest.mark.parametrize("field", ["strike_pct", "ki_pct", "autocall_pct"])
def test_solve_by_bisection_reaches_target_pv(field):
    sr = solve(terms(), field)
    assert sr.iterations > 0
    assert sr.pv == pytest.approx(1.0 - sr.terms.rebate, abs=2e-4)
    assert getattr(sr.terms, field) == pytest.approx(sr.value)


def test_solved_strike_respects_ki_constraint():
    sr = solve(terms(ki_pct=0.75), "strike_pct")
    assert sr.terms.strike_pct > sr.terms.ki_pct
    assert 0.50 <= sr.value <= 1.00


def test_solved_ki_stays_below_strike():
    sr = solve(terms(strike_pct=0.85), "ki_pct")
    assert sr.terms.ki_pct < sr.terms.strike_pct


def test_solve_lower_call_for_upside_product():
    """已知配息，反解券商能把「參與表現價」壓到多低。"""
    t = terms(coupon_pa=0.145, strike_pct=0.80, ki_type=KIType.NONE, ki_pct=None,
              lower_call_strike_pct=1.03, rebate=0.01)
    sr = solve(t, "lower_call_strike_pct")
    assert sr.terms.lower_call_strike_pct > sr.terms.strike_pct
    assert sr.pv == pytest.approx(1.0 - t.rebate, abs=2e-4)


def test_solve_rejects_unknown_field():
    with pytest.raises(ValueError, match="不支援求解欄位"):
        solve(terms(), "tenor_months")


def test_solve_requires_coupon_for_non_coupon_fields():
    with pytest.raises(ValueError, match="需要已知的 coupon_pa"):
        solve(terms(coupon_pa=None), "strike_pct")


def test_solve_lower_call_requires_upside_product():
    with pytest.raises(ValueError, match="僅 Upside FCN"):
        solve(terms(), "lower_call_strike_pct")


def test_solve_reports_when_no_solution_in_range():
    """配息高得離譜時，任何執行價都無法把價值壓回面額，須明確報告無解。"""
    with pytest.raises(ValueError, match="無解"):
        solve(terms(coupon_pa=2.00), "strike_pct")

    # 配息低得離譜時同理（連最保守的執行價都補不回來）
    with pytest.raises(ValueError, match="無解"):
        solve(terms(coupon_pa=0.01), "strike_pct")


def test_solve_is_deterministic():
    a = solve(terms(), "strike_pct")
    b = solve(terms(), "strike_pct")
    assert a.value == pytest.approx(b.value, rel=1e-12)


def test_solved_strike_is_higher_when_coupon_is_higher():
    """配息開得越高，券商必須把執行價往上調（保護變薄）才回得到面額。"""
    lo = solve(terms(coupon_pa=0.13), "strike_pct")
    hi = solve(terms(coupon_pa=0.16), "strike_pct")
    assert hi.value > lo.value
