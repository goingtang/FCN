"""Term Sheet 對帳測試（合成行情，不依賴網路）。"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from fcn.engine import BarrierLevels, Scenario, evaluate
from fcn.reconcile import (
    TermSheet,
    detect_split_ratios,
    format_reconciliation,
    nearest_clean_ratio,
    reconcile,
)
from fcn.data import Bars, MarketData
from fcn.schedule import build_schedule
from fcn.terms import FCNTerms, KIType, KOType

SYMS = ["AAA", "BBB"]
TRADE = pd.Timestamp("2024-01-02")


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
    )
    d.update(kw)
    return FCNTerms(**d)


def make_md(paths: dict[str, list[float]], days: pd.DatetimeIndex) -> MarketData:
    closes = pd.DataFrame({s: pd.Series(v, index=days) for s, v in paths.items()})
    bars = {
        s: Bars(symbol=s, close=closes[s], adjclose=closes[s],
                dividends=pd.Series(dtype="float64", index=pd.DatetimeIndex([])),
                currency="USD", exchange="TEST")
        for s in closes.columns
    }
    return MarketData(closes=closes, adjcloses=closes, bars=bars,
                      symbol_map={s: s for s in closes.columns})


def scenario_md(n: int = 400):
    """AAA 全程平穩、BBB 中途破 KI 且期末低於執行價 -> 到期承接 BBB。"""
    days = pd.bdate_range(TRADE, periods=n, name="date")
    aaa = np.full(n, 90.0)
    bbb = np.full(n, 90.0)
    aaa[0] = bbb[0] = 100.0
    bbb[60] = 55.0                       # 破 KI 60
    md = make_md({"AAA": list(aaa), "BBB": list(bbb)}, days)
    sch = build_schedule(terms(), md.trading_days, TRADE)
    md.closes.loc[sch.final_valuation] = [95.0, 70.0]     # BBB 期末最差且 < 執行價
    return md, sch


# --------------------------------------------------------------------------
# 倍數偵測
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,clean",
    [(1.0, 1.0), (0.9998, 1.0), (10.0004, 10.0), (9.99838, 10.0),
     (0.25, 0.25), (1.5, 1.5), (4.0, 4.0), (0.3333, 1 / 3)],
)
def test_nearest_clean_ratio(raw, clean):
    assert nearest_clean_ratio(raw) == pytest.approx(clean, rel=1e-3)


def test_detect_split_ratio():
    md, _ = scenario_md()
    ratios = detect_split_ratios({"AAA": 100.0, "BBB": 1000.0}, md.closes, TRADE)
    assert ratios["AAA"] == pytest.approx(1.0)
    assert ratios["BBB"] == pytest.approx(10.0)


# --------------------------------------------------------------------------
# 絕對價位
# --------------------------------------------------------------------------


def test_explicit_levels_override_percentages():
    """Term Sheet 載明的四捨五入價位優先於「期初價 x 百分比」。"""
    md, sch = scenario_md()
    lv = BarrierLevels(
        initial={"AAA": 100.0, "BBB": 100.0},
        strike={"AAA": 80.0, "BBB": 81.5},          # BBB 執行價經四捨五入
        ki={"AAA": 60.0, "BBB": 60.0},
        ko={"AAA": 100.0, "BBB": 100.0},
    )
    out = evaluate(terms(), md.closes, sch, levels=lv)
    assert out.scenario is Scenario.DELIVERY
    assert out.delivered_symbol == "BBB"
    assert out.delivery_price == pytest.approx(81.5)     # 非 80.0
    assert out.shares == np.floor(100_000 / 81.5)


def test_missing_initial_price_is_rejected():
    md, sch = scenario_md()
    with pytest.raises(ValueError, match="缺少期初價"):
        evaluate(terms(), md.closes, sch, levels=BarrierLevels(initial={"AAA": 100.0}))


def test_incomplete_level_set_is_rejected():
    md, sch = scenario_md()
    with pytest.raises(ValueError, match="價位不完整"):
        evaluate(terms(), md.closes, sch,
                 levels=BarrierLevels(initial={"AAA": 100.0, "BBB": 100.0},
                                      strike={"AAA": 80.0}))


# --------------------------------------------------------------------------
# 對帳
# --------------------------------------------------------------------------


def sheet(**expected) -> TermSheet:
    return TermSheet(
        terms=terms(),
        trade_date=TRADE,
        levels=BarrierLevels(
            initial={"AAA": 100.0, "BBB": 100.0},
            ko={"AAA": 100.0, "BBB": 100.0},
            strike={"AAA": 80.0, "BBB": 80.0},
            ki={"AAA": 60.0, "BBB": 60.0},
        ),
        **expected,
    )


def test_reconcile_all_pass():
    md, _ = scenario_md()
    rec = reconcile(
        sheet(expected_scenario="到期承接股票", expected_delivered_symbol="BBB",
              expected_delivery_price=80.0, expected_coupon_total=12_000.0),
        md,
    )
    assert rec.passed, [c.item for c in rec.failures]
    assert len(rec.compared) >= 8
    assert "PASS" in format_reconciliation(rec)


def test_reconcile_flags_wrong_expectation():
    md, _ = scenario_md()
    rec = reconcile(sheet(expected_scenario="提前出場", expected_coupon_total=99.0), md)
    assert not rec.passed
    items = {c.item for c in rec.failures}
    assert "情境" in items and "配息合計" in items


def test_reconcile_rescales_market_to_term_sheet_scale():
    """文件為分割前尺度時，行情須被還原，且整股數在原始尺度上計算。"""
    md, sch = scenario_md()
    ts = TermSheet(
        terms=terms(),
        trade_date=TRADE,
        levels=BarrierLevels(
            initial={"AAA": 1000.0, "BBB": 1000.0},      # 行情的 10 倍
            ko={"AAA": 1000.0, "BBB": 1000.0},
            strike={"AAA": 800.0, "BBB": 800.0},
            ki={"AAA": 600.0, "BBB": 600.0},
        ),
        expected_delivered_symbol="BBB",
        expected_delivery_price=800.0,
    )
    rec = reconcile(ts, md)
    assert rec.passed, [c.item for c in rec.failures]
    assert rec.split_ratios["BBB"] == pytest.approx(10.0)
    assert rec.outcome.delivery_price == pytest.approx(800.0)
    assert rec.outcome.shares == np.floor(100_000 / 800.0)
    assert any("分割" in n for n in rec.notes)

    # 若不換算，整股數會落在錯誤的尺度上
    raw = reconcile(ts, md, auto_adjust_splits=False)
    assert not raw.passed
    assert any("未自動換算" in n for n in raw.notes)


def test_reconcile_detects_wrong_initial_price():
    """文件期初價與市場收盤不符（且非乾淨的分割倍數）時必須抓出來。"""
    md, _ = scenario_md()
    ts = sheet()
    ts.levels.initial["BBB"] = 103.7          # 與市場 100 差 3.7%，非乾淨倍數
    rec = reconcile(ts, md)
    assert not rec.passed
    assert any("期初價" in c.item or "價格尺度" in c.item for c in rec.failures)


# --------------------------------------------------------------------------
# JSON 規格
# --------------------------------------------------------------------------


def test_from_dict_parses_percentages():
    d = {
        "trade_date": "2024-01-02",
        "terms": {
            "underlyings": SYMS, "tenor_months": 12, "coupon_pa": 12,
            "strike_pct": 80, "ko_type": "Daily Memory", "autocall_pct": 100,
            "ki_type": "AKI", "ki_pct": 60, "rebate": 3, "notional": 100000,
        },
        "levels": {"initial": {"AAA": 100.0, "BBB": 100.0}},
        "expected": {"scenario": "到期承接股票"},
    }
    ts = TermSheet.from_dict(d)
    assert ts.terms.coupon_pa == pytest.approx(0.12)
    assert ts.terms.strike_pct == pytest.approx(0.80)
    assert ts.terms.ki_pct == pytest.approx(0.60)
    assert ts.terms.rebate == pytest.approx(0.03)
    assert ts.expected_scenario == "到期承接股票"
    assert ts.levels.strike is None          # 未提供 -> 由百分比推算


def test_from_dict_handles_na_ki():
    d = {
        "trade_date": "2024-01-02",
        "terms": {"underlyings": SYMS, "tenor_months": 12, "coupon_pa": 9,
                  "strike_pct": 85, "ki_type": "NA", "autocall_pct": 100,
                  "ko_type": "Monthly", "lower_call_strike_pct": 103},
        "levels": {"initial": {"AAA": 100.0, "BBB": 100.0}},
    }
    ts = TermSheet.from_dict(d)
    assert ts.terms.ki_type is KIType.NONE
    assert ts.terms.ki_pct is None
    assert ts.terms.is_upside
    assert ts.terms.lower_call_strike_pct == pytest.approx(1.03)


def test_bundled_example_spec_is_valid():
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / "examples" / "termsheet_sample.json"
    ts = TermSheet.from_dict(json.loads(p.read_text(encoding="utf-8")))
    assert ts.terms.underlyings == ["TSM UN", "NVDA UW", "MSFT UW"]
    assert ts.expected_scenario == "到期承接股票"
    # 文件內部一致性：執行價 = 期初價 x 80%
    for s in ts.levels.initial:
        assert ts.levels.strike[s] == pytest.approx(ts.levels.initial[s] * 0.80, abs=0.01)
