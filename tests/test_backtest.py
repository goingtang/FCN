"""回測模組測試（使用合成行情，不依賴網路）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fcn.backtest import run_backtest
from fcn.data import Bars, MarketData, to_yahoo_symbol
from fcn.engine import Scenario
from fcn.terms import FCNTerms, KIType, KOType

SYMS = ["AAA", "BBB"]


def synthetic_md(n_days: int = 900, seed: int = 3) -> MarketData:
    days = pd.bdate_range("2020-01-02", periods=n_days, name="date")
    rng = np.random.default_rng(seed)
    data = {}
    for i, s in enumerate(SYMS):
        logret = rng.normal(0.0003, 0.02, n_days)
        px = 100.0 * np.exp(np.cumsum(logret))
        data[s] = pd.Series(px, index=days, name=s)

    closes = pd.DataFrame(data)
    bars = {
        s: Bars(
            symbol=s,
            close=closes[s],
            adjclose=closes[s],
            dividends=pd.Series(dtype="float64"),
            currency="USD",
            exchange="TEST",
        )
        for s in SYMS
    }
    return MarketData(
        closes=closes, adjcloses=closes, bars=bars, symbol_map={s: s for s in SYMS}
    )


def terms(**kw) -> FCNTerms:
    d = dict(
        underlyings=SYMS,
        tenor_months=6,
        coupon_pa=0.12,
        strike_pct=0.80,
        ko_type=KOType.DAILY_MEMORY,
        autocall_pct=1.00,
        ki_type=KIType.AKI,
        ki_pct=0.60,
    )
    d.update(kw)
    return FCNTerms(**d)


def test_backtest_runs_and_summarises():
    md = synthetic_md()
    bt = run_backtest(terms(), md, step_days=10)

    assert len(bt.outcomes) > 20
    s = bt.summary()
    assert s["樣本數"] == len(bt.outcomes)
    assert 0.0 <= s["勝率"] <= 1.0
    probs = s["提前出場比例"] + s["到期還本比例"] + s["承接股票比例"]
    assert probs == pytest.approx(1.0)

    df = bt.to_frame()
    assert len(df) == len(bt.outcomes)
    assert df["報酬率"].notna().all()


def test_backtest_never_extends_past_available_data():
    md = synthetic_md()
    bt = run_backtest(terms(), md, step_days=10)
    last = md.trading_days[-1]
    assert all(o.schedule.final_valuation <= last for o in bt.outcomes)


def test_backtest_requires_coupon():
    with pytest.raises(ValueError, match="需要 terms.coupon_pa"):
        run_backtest(terms(coupon_pa=None), synthetic_md())


def test_return_is_capped_by_coupon_unless_delivered():
    md = synthetic_md()
    bt = run_backtest(terms(), md, step_days=10)
    for o in bt.outcomes:
        if o.scenario is not Scenario.DELIVERY:
            assert o.return_pct <= 0.12 + 1e-9


def test_scenario_breakdown_columns():
    bt = run_backtest(terms(), synthetic_md(), step_days=20)
    bd = bt.scenario_breakdown()
    assert list(bd.columns) == ["次數", "占比", "平均報酬", "最差", "最佳"]
    assert bd["次數"].sum() == len(bt.outcomes)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("TSM UN", "TSM"),
        ("NVDA UW", "NVDA"),
        ("MSFT UW", "MSFT"),
        ("2330 TT", "2330.TW"),
        ("700 HK", "0700.HK"),
        ("7203 JP", "7203.T"),
        ("AAPL", "AAPL"),
        ("0050.TW", "0050.TW"),
    ],
)
def test_bloomberg_ticker_mapping(raw, expected):
    assert to_yahoo_symbol(raw) == expected


def test_unknown_exchange_code_raises():
    with pytest.raises(ValueError, match="未知的交易所代碼"):
        to_yahoo_symbol("FOO ZZ")
