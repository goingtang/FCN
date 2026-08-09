"""Upside FCN（可參與漲幅）測試。

永豐金證券範例條件：
  UD = TSM UN / AMD UW / NVDA UW，USD，12M，Cpn 9%，I Delay 5BD，Monthly，
  Put Strike 85%，KO Type Monthly（非記憶式），Autocall 100%，
  Lower Call Strike 103%，KI Type NA / KI Level NA

還本金額 = 面額 x [100% + 100% x max(0%, 最差表現之連結標的表現 - 參與表現價%)]
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fcn import mc
from fcn.engine import Scenario, evaluate
from fcn.mktcal import future_trading_days
from fcn.schedule import build_schedule
from fcn.terms import CouponFreq, FCNTerms, KIType, KOType

SYMS = ["TSM", "AMD", "NVDA"]
TRADE_DATE = pd.Timestamp("2026-01-05")

_CODE_TO_SCENARIO = {
    mc.AUTOCALL: Scenario.AUTOCALL,
    mc.NO_KI: Scenario.MATURITY_NO_KI,
    mc.ABOVE_STRIKE: Scenario.MATURITY_ABOVE_STRIKE,
    mc.DELIVERY: Scenario.DELIVERY,
}


def upside_terms(**kw) -> FCNTerms:
    """永豐金 Upside FCN 範例條款。"""
    d = dict(
        underlyings=SYMS,
        tenor_months=12,
        coupon_pa=0.09,
        issue_delay_bd=5,
        coupon_freq=CouponFreq.MONTHLY,
        strike_pct=0.85,
        ko_type=KOType.MONTHLY,          # 非記憶式，須同日全數 >= KO
        autocall_pct=1.00,
        ki_type=KIType.NONE,             # KI Type = NA
        ki_pct=None,
        lower_call_strike_pct=1.03,      # Lower Call Strike 103%
        participation=1.00,
        notional=100_000.0,
        integer_shares=False,
    )
    d.update(kw)
    return FCNTerms(**d)


def build(terms: FCNTerms, n: int = 400, quiet: float = 90.0):
    days = pd.bdate_range("2024-01-02", periods=n, name="date")
    px = pd.DataFrame(quiet, index=days, columns=SYMS, dtype=float)
    px.iloc[0] = 100.0
    sch = build_schedule(terms, days, days[0])
    return px, sch


def test_product_naming_and_validation():
    t = upside_terms()
    assert t.is_upside
    assert t.product_name == "Upside FCN"
    assert "Lower Call 103%" in t.describe()

    plain = upside_terms(lower_call_strike_pct=None)
    assert not plain.is_upside
    assert plain.product_name == "FCN"

    # 參與表現價須高於執行價
    with pytest.raises(ValueError, match="須高於執行價"):
        upside_terms(lower_call_strike_pct=0.80, strike_pct=0.85)


def test_upside_payment_formula():
    t = upside_terms()
    assert t.upside_payment(1.03) == pytest.approx(0.0)      # 恰好等於參與表現價
    assert t.upside_payment(0.95) == pytest.approx(0.0)      # 低於參與表現價 -> 0
    assert t.upside_payment(1.20) == pytest.approx(100_000 * 0.17)
    # 參與率 50%
    half = upside_terms(participation=0.50)
    assert half.upside_payment(1.20) == pytest.approx(100_000 * 0.5 * 0.17)


def test_maturity_with_upside_participation():
    """到期最差標的 118%：還本 + 參與 (118% - 103%) = 15%。"""
    t = upside_terms()
    px, sch = build(t)
    px.loc[sch.final_valuation] = [125.0, 118.0, 130.0]      # AMD 最差 118%

    out = evaluate(t, px, sch)
    # KI Type = NA 時視同 KI 恆已觸發，直接以期末 vs 執行價判定
    assert out.scenario is Scenario.MATURITY_ABOVE_STRIKE
    assert out.worst_symbol == "AMD"
    assert out.upside_payment == pytest.approx(100_000 * 0.15)
    assert out.coupon_total == pytest.approx(9_000.0)
    assert out.total_value == pytest.approx(100_000 + 15_000 + 9_000)
    assert out.return_pct == pytest.approx(0.24)


def test_upside_is_zero_between_autocall_and_participation_level():
    """最差表現落在提前出場價(100%)與參與表現價(103%)之間 -> 無漲幅。"""
    t = upside_terms()
    px, sch = build(t)
    px.loc[sch.final_valuation] = [102.0, 101.0, 105.0]

    out = evaluate(t, px, sch)
    assert out.upside_payment == pytest.approx(0.0)
    assert out.return_pct == pytest.approx(0.09)             # 只有配息


def test_autocall_also_participates_in_upside():
    """提前出場同樣返還「贖回金額（含漲幅）及應計配息」。"""
    t = upside_terms()
    px, sch = build(t)
    d = sch.ko_days[2]
    px.loc[d] = [110.0, 108.0, 115.0]                        # 同日全數 >= KO，最差 108%

    out = evaluate(t, px, sch)
    assert out.scenario is Scenario.AUTOCALL
    assert out.exit_date == d
    assert out.upside_payment == pytest.approx(100_000 * 0.05)   # 108% - 103%
    assert out.principal_returned == 100_000


def test_delivery_has_no_upside():
    """承接情境的最差表現必低於執行價，漲幅恆為 0。"""
    t = upside_terms()
    px, sch = build(t)
    px.loc[sch.final_valuation] = [95.0, 60.0, 99.0]         # AMD 60% < 執行價 85%

    out = evaluate(t, px, sch)
    assert out.scenario is Scenario.DELIVERY
    assert out.delivered_symbol == "AMD"
    assert out.upside_payment == 0.0
    assert out.delivery_price == pytest.approx(85.0)
    assert out.stock_value == pytest.approx(100_000 / 85.0 * 60.0)


def test_monthly_ko_is_non_memory_and_observes_period_ends():
    """KO Type = Monthly：非記憶式，且觀察日為每個配息期結束日。"""
    t = upside_terms()
    px, sch = build(t)

    # 每月一個觀察日，且與配息日對齊
    assert len(sch.ko_days) <= t.tenor_months
    coupon_set = set(sch.coupon_dates)
    for d in sch.ko_days:
        assert min(abs((d - c).days) for c in coupon_set) <= 4

    # 不同觀察日各自站上 KO 不成立（非記憶式）
    px.loc[sch.ko_days[1], "TSM"] = 105.0
    px.loc[sch.ko_days[2], "AMD"] = 105.0
    px.loc[sch.ko_days[3], "NVDA"] = 105.0
    px.loc[sch.final_valuation] = 90.0
    assert evaluate(t, px, sch).scenario is not Scenario.AUTOCALL

    # 同一觀察日全數站上才成立
    px.loc[sch.ko_days[5], :] = 105.0
    assert evaluate(t, px, sch).scenario is Scenario.AUTOCALL


def test_ko_between_monthly_observation_days_is_ignored():
    """非觀察日衝上 KO 不算數。"""
    t = upside_terms()
    px, sch = build(t)
    between = px.index[(px.index > sch.ko_days[1]) & (px.index < sch.ko_days[2])]
    px.loc[between, :] = 130.0
    px.loc[sch.final_valuation] = 90.0

    assert evaluate(t, px, sch).scenario is not Scenario.AUTOCALL


# --------------------------------------------------------------------------
# 向量化引擎一致性 + 定價
# --------------------------------------------------------------------------


def market(vol: float = 0.45) -> mc.MarketParams:
    n = len(SYMS)
    return mc.MarketParams(
        symbols=SYMS,
        spot=np.array([200.0, 150.0, 180.0]),
        vol=np.full(n, vol),
        div_yield=np.zeros(n),
        corr=np.full((n, n), 0.5) + np.eye(n) * 0.5,
        rate=0.04,
        drift=np.full(n, 0.08),
    )


@pytest.mark.parametrize(
    "kw",
    [
        {},
        {"ko_type": KOType.DAILY_MEMORY},
        {"ki_type": KIType.AKI, "ki_pct": 0.60},
        {"participation": 0.50},
        {"lower_call_strike_pct": 1.00},
    ],
)
def test_vectorized_matches_reference_for_upside(kw):
    t = upside_terms(**kw)
    cal = future_trading_days(TRADE_DATE, t.tenor_months + 2)
    sch = build_schedule(t, cal, TRADE_DATE)
    grid = mc.build_grid(t, sch, cal)
    mp = market()

    rng = np.random.default_rng(11)
    paths = mc._simulate_chunk(mp, grid, 250, rng, risk_neutral=False)
    res = mc._evaluate_paths(t, grid, paths)

    for i in range(paths.shape[0]):
        df = pd.DataFrame(paths[i], index=grid.dates, columns=SYMS)
        ref = evaluate(t, df, sch)
        assert _CODE_TO_SCENARIO[int(res.scenario[i])] is ref.scenario, f"path {i}"

        ref_redemption = (
            ref.principal_returned + ref.stock_value + ref.residual_cash + ref.upside_payment
        ) / t.notional
        assert res.redemption[i] == pytest.approx(ref_redemption, rel=1e-9), f"path {i}"


def test_upside_feature_lowers_fair_coupon():
    """上檔參與要花錢買，因此公允配息率必定低於同條件的一般 FCN。"""
    mp = market()
    plain = mc.price(
        upside_terms(coupon_pa=None, lower_call_strike_pct=None),
        mp, trade_date=TRADE_DATE, n_paths=30_000,
    )
    upside = mc.price(
        upside_terms(coupon_pa=None), mp, trade_date=TRADE_DATE, n_paths=30_000
    )
    assert upside.fair_coupon_pa < plain.fair_coupon_pa


def test_higher_participation_level_raises_fair_coupon():
    """參與表現價越高（上檔越難吃到）-> 該選擇權越便宜 -> 配息回升。"""
    mp = market()
    low = mc.price(
        upside_terms(coupon_pa=None, lower_call_strike_pct=1.03),
        mp, trade_date=TRADE_DATE, n_paths=30_000,
    )
    high = mc.price(
        upside_terms(coupon_pa=None, lower_call_strike_pct=1.30),
        mp, trade_date=TRADE_DATE, n_paths=30_000,
    )
    assert high.fair_coupon_pa > low.fair_coupon_pa


def test_upside_forecast_return_exceeds_coupon_cap():
    """一般 FCN 報酬上限為配息；Upside FCN 可突破此上限。"""
    fc = mc.forecast(upside_terms(), market(), trade_date=TRADE_DATE, n_paths=30_000)
    assert fc.returns.max() > 0.09
