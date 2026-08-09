"""向量化蒙地卡羅 vs 單路徑參考實作的交叉驗證，以及定價的合理性檢查。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fcn import mc
from fcn.engine import Scenario, evaluate
from fcn.mktcal import future_trading_days
from fcn.schedule import build_schedule
from fcn.terms import AutocallCoupon, CouponFreq, FCNTerms, KIType, KOType

SYMS = ["AAA", "BBB", "CCC"]
TRADE_DATE = pd.Timestamp("2026-01-05")

_CODE_TO_SCENARIO = {
    mc.AUTOCALL: Scenario.AUTOCALL,
    mc.NO_KI: Scenario.MATURITY_NO_KI,
    mc.ABOVE_STRIKE: Scenario.MATURITY_ABOVE_STRIKE,
    mc.DELIVERY: Scenario.DELIVERY,
}


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
        notional=100_000.0,
        integer_shares=False,
        autocall_coupon=AutocallCoupon.ACCRUED,
    )
    d.update(kw)
    return FCNTerms(**d)


def market(vol: float = 0.40, rate: float = 0.04) -> mc.MarketParams:
    n = len(SYMS)
    return mc.MarketParams(
        symbols=SYMS,
        spot=np.array([100.0, 200.0, 50.0]),
        vol=np.full(n, vol),
        div_yield=np.zeros(n),
        corr=np.full((n, n), 0.3) + np.eye(n) * 0.7,
        rate=rate,
        drift=np.full(n, 0.08),
    )


def setup(t: FCNTerms):
    cal = future_trading_days(TRADE_DATE, t.tenor_months + 2)
    sch = build_schedule(t, cal, TRADE_DATE)
    return cal, sch, mc.build_grid(t, sch, cal)


@pytest.mark.parametrize(
    "kw",
    [
        {},
        {"ki_type": KIType.EKI},
        {"ki_type": KIType.NONE, "ki_pct": None},
        {"ko_type": KOType.DAILY},
        {"ko_type": KOType.MONTHLY_MEMORY},
        {"ko_type": KOType.NONE},
        {"autocall_coupon": AutocallCoupon.FULL_PERIODS},
        {"strike_pct": 0.95, "ki_pct": 0.75, "autocall_pct": 1.05},
    ],
)
def test_vectorized_matches_reference_engine(kw):
    """對同一批隨機路徑，向量化判定須與 engine.evaluate 完全一致。"""
    t = terms(**kw)
    cal, sch, grid = setup(t)
    mp = market()

    rng = np.random.default_rng(7)
    paths = mc._simulate_chunk(mp, grid, 300, rng, risk_neutral=False)
    res = mc._evaluate_paths(t, grid, paths)
    annuity, nominal = mc._coupon_factors(t, grid, res, mp.rate)

    for i in range(paths.shape[0]):
        df = pd.DataFrame(paths[i], index=grid.dates, columns=SYMS)
        ref = evaluate(t, df, sch)

        assert _CODE_TO_SCENARIO[int(res.scenario[i])] is ref.scenario, f"path {i}"

        ref_redemption = (
            ref.principal_returned + ref.stock_value + ref.residual_cash
        ) / t.notional
        assert res.redemption[i] == pytest.approx(ref_redemption, rel=1e-9), f"path {i}"

        assert grid.dates[res.exit_step[i]] == ref.exit_date, f"path {i}"

        # 配息：每單位配息率的名目總額 x 實際配息率 x 面額
        assert nominal[i] * t.coupon_pa * t.notional == pytest.approx(
            ref.coupon_total, rel=1e-9
        ), f"path {i}"


def test_fair_coupon_is_positive_and_rises_with_vol():
    """波動度越高 -> 賣出賣權的權利金越高 -> 公允配息率越高。"""
    t = terms(coupon_pa=None)
    lo = mc.price(t, market(vol=0.25), trade_date=TRADE_DATE, n_paths=20_000)
    hi = mc.price(t, market(vol=0.60), trade_date=TRADE_DATE, n_paths=20_000)

    assert 0.0 < lo.fair_coupon_pa < 1.0
    assert hi.fair_coupon_pa > lo.fair_coupon_pa


def test_fair_coupon_rises_with_strike():
    """執行價越高（保護越薄）-> 公允配息率越高。"""
    thin = mc.price(
        terms(coupon_pa=None, strike_pct=0.95), market(), trade_date=TRADE_DATE, n_paths=20_000
    )
    thick = mc.price(
        terms(coupon_pa=None, strike_pct=0.70), market(), trade_date=TRADE_DATE, n_paths=20_000
    )
    assert thin.fair_coupon_pa > thick.fair_coupon_pa


def test_more_underlyings_raises_fair_coupon():
    """worst-of 反分散：標的越多，最差表現越差，公允配息率越高。"""
    mp1 = mc.MarketParams(
        symbols=["A"], spot=np.array([100.0]), vol=np.array([0.40]),
        div_yield=np.zeros(1), corr=np.eye(1), rate=0.04,
    )
    n = 4
    mp4 = mc.MarketParams(
        symbols=list("ABCD"), spot=np.full(n, 100.0), vol=np.full(n, 0.40),
        div_yield=np.zeros(n), corr=np.full((n, n), 0.3) + np.eye(n) * 0.7, rate=0.04,
    )
    one = mc.price(
        terms(underlyings=["A"], coupon_pa=None), mp1, trade_date=TRADE_DATE, n_paths=20_000
    )
    four = mc.price(
        terms(underlyings=list("ABCD"), coupon_pa=None), mp4,
        trade_date=TRADE_DATE, n_paths=20_000,
    )
    assert four.fair_coupon_pa > one.fair_coupon_pa


def test_lower_correlation_raises_fair_coupon():
    """相關性越低 -> 至少一檔重挫的機率越高 -> 配息越高。"""
    def mp(rho: float) -> mc.MarketParams:
        n = 3
        return mc.MarketParams(
            symbols=SYMS, spot=np.full(n, 100.0), vol=np.full(n, 0.40),
            div_yield=np.zeros(n),
            corr=np.full((n, n), rho) + np.eye(n) * (1 - rho), rate=0.04,
        )

    high = mc.price(terms(coupon_pa=None), mp(0.9), trade_date=TRADE_DATE, n_paths=20_000)
    low = mc.price(terms(coupon_pa=None), mp(0.1), trade_date=TRADE_DATE, n_paths=20_000)
    assert low.fair_coupon_pa > high.fair_coupon_pa


def test_pricing_at_fair_coupon_gives_par():
    """以公允配息率回代，理論價值應等於面額 100%。"""
    t = terms(coupon_pa=None)
    mp = market()
    res = mc.price(t, mp, trade_date=TRADE_DATE, n_paths=30_000)
    repriced = mc.price(
        t.with_coupon(res.fair_coupon_pa), mp, trade_date=TRADE_DATE, n_paths=30_000
    )
    assert repriced.pv_at_quote == pytest.approx(1.0, abs=1e-6)


def test_underquoted_coupon_shows_negative_value_gap():
    """券商給的配息低於公允值時，理論價值低於面額。"""
    t = terms(coupon_pa=None)
    mp = market()
    fair = mc.price(t, mp, trade_date=TRADE_DATE, n_paths=20_000).fair_coupon_pa
    stingy = mc.price(
        t.with_coupon(fair * 0.6), mp, trade_date=TRADE_DATE, n_paths=20_000
    )
    assert stingy.value_gap_pct is not None and stingy.value_gap_pct < 0


def test_forecast_returns_are_capped_by_total_coupon():
    """未承接股票時，報酬上限就是配息總額。"""
    t = terms(coupon_pa=0.12)
    fc = mc.forecast(t, market(), trade_date=TRADE_DATE, n_paths=20_000)
    not_delivered = fc.returns[fc.scenario != mc.DELIVERY]
    assert not_delivered.max() <= 0.12 + 1e-9
    assert fc.returns.min() < 0        # 必定存在承接虧損的路徑
    assert set(fc.scenario_probs) == set(mc._SCENARIO_LABELS)
