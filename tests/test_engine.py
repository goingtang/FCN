"""規則引擎單元測試：四大情境 + 條款註記的四個陷阱 + 邊界條件。

夾具約定
--------
- 期初價（交易日收盤）固定為 100 -> KO=100、執行價=80、KI=60
- 其餘日子預設 90：低於 KO、高於執行價與 KI，故預設情境為「到期還本」
- 期末價一律寫在 ``sch.final_valuation``，不可用 ``iloc[-1]``
  （行情資料長度大於契約天期，最後一列並非期末評價日）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fcn.engine import Scenario, evaluate
from fcn.schedule import build_schedule
from fcn.terms import AutocallCoupon, CouponFreq, FCNTerms, KIType, KOType

SYMS = ["AAA", "BBB", "CCC"]
INITIAL = 100.0
QUIET = 90.0


def make_days(n: int = 400, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n, name="date")


def base_terms(**kw) -> FCNTerms:
    defaults = dict(
        underlyings=SYMS,
        tenor_months=12,
        coupon_pa=0.12,
        issue_delay_bd=5,
        coupon_freq=CouponFreq.MONTHLY,
        strike_pct=0.80,
        ko_type=KOType.DAILY_MEMORY,
        autocall_pct=1.00,
        ki_type=KIType.AKI,
        ki_pct=0.60,
        notional=100_000.0,
        integer_shares=False,
    )
    defaults.update(kw)
    return FCNTerms(**defaults)


def build(terms: FCNTerms | None = None, n: int = 400):
    """建立 (價格表, 條款, 日程)；價格表期初 100、其餘 90。"""
    terms = terms or base_terms()
    days = make_days(n)
    px = pd.DataFrame(QUIET, index=days, columns=SYMS, dtype=float)
    px.iloc[0] = INITIAL
    sch = build_schedule(terms, days, days[0])
    return px, terms, sch


# --------------------------------------------------------------------------
# 四大情境
# --------------------------------------------------------------------------


def test_autocall_memory_across_different_days():
    """記憶式：三檔在不同日子各自觸及 KO，最後一檔觸及當日提前出場。"""
    px, terms, sch = build()
    d = sch.ko_days
    px.loc[d[10], "AAA"] = 101.0
    px.loc[d[20], "BBB"] = 102.0
    px.loc[d[30], "CCC"] = 100.0            # 邊界：等於 KO 即觸發

    out = evaluate(terms, px, sch)

    assert out.scenario is Scenario.AUTOCALL
    assert out.exit_date == d[30]
    assert out.memory_dates == {"AAA": d[10], "BBB": d[20], "CCC": d[30]}
    assert out.principal_returned == terms.notional
    expected = terms.notional * 0.12 * (d[30] - sch.issue_date).days / 365.0
    assert out.coupon_total == pytest.approx(expected, rel=1e-9)


def test_maturity_no_ki_below_strike_returns_principal():
    """情境 B：從未跌破 KI，期末低於執行價，仍全額還本。"""
    px, terms, sch = build()
    px.iloc[1:] = 70.0                      # 全程在 KI(60) 之上、執行價(80) 之下

    out = evaluate(terms, px, sch)

    assert out.scenario is Scenario.MATURITY_NO_KI
    assert not out.ki_occurred
    assert out.principal_returned == terms.notional
    assert out.coupon_total == pytest.approx(12_000.0)
    assert out.return_pct == pytest.approx(0.12)


def test_maturity_ki_but_recovers_above_strike():
    """情境 A：曾跌破 KI，期末站回執行價之上，仍全額還本。"""
    px, terms, sch = build()
    px.loc[sch.ki_days[30], "AAA"] = 55.0   # 跌破 KI 60
    px.loc[sch.final_valuation] = 90.0      # 期末 > 執行價 80

    out = evaluate(terms, px, sch)

    assert out.scenario is Scenario.MATURITY_ABOVE_STRIKE
    assert out.ki_occurred
    assert out.ki_dates["AAA"] == sch.ki_days[30]
    assert out.principal_returned == terms.notional


def test_delivery_of_worst_performer():
    """情境 C：曾跌破 KI 且期末低於執行價 -> 以執行價承接最差標的。"""
    px, terms, sch = build()
    px.loc[sch.ki_days[30], "AAA"] = 55.0
    px.loc[sch.final_valuation] = [95.0, 92.0, 50.0]   # CCC 最差且 < 執行價

    out = evaluate(terms, px, sch)

    assert out.scenario is Scenario.DELIVERY
    assert out.delivered_symbol == "CCC"
    assert out.delivery_price == pytest.approx(80.0)
    assert out.shares == pytest.approx(100_000 / 80.0)
    assert out.stock_value == pytest.approx(100_000 / 80.0 * 50.0)
    assert out.total_value == pytest.approx(62_500.0 + 12_000.0)
    assert out.return_pct == pytest.approx(-0.255)
    # 對照：直接持有最差標的為 -50%
    assert out.buy_and_hold_return() == pytest.approx(-0.50)


# --------------------------------------------------------------------------
# 條款註記的四個陷阱
# --------------------------------------------------------------------------


def test_memory_event_does_not_protect_from_delivery():
    """註記：曾記憶之標的，到期若為最差且低於執行價，仍須承接。"""
    px, terms, sch = build()
    px.loc[sch.ko_days[10], "CCC"] = 130.0              # CCC 先發生記憶事件
    px.loc[sch.ki_days[60], "AAA"] = 55.0               # AAA 觸發 KI
    px.loc[sch.final_valuation] = [95.0, 95.0, 40.0]    # CCC 期末最差

    out = evaluate(terms, px, sch)

    assert out.memory_dates["CCC"] == sch.ko_days[10]   # 確實記憶過
    assert out.scenario is Scenario.DELIVERY
    assert out.delivered_symbol == "CCC"                # 記憶不提供保護
    assert any("記憶事件" in w for w in out.warnings)


def test_delivered_symbol_need_not_be_the_ki_trigger():
    """註記：承接標的為表現最差者，不必然是觸發 KI 的那一檔。"""
    px, terms, sch = build()
    px.loc[sch.ki_days[30], "AAA"] = 55.0               # 只有 AAA 觸發 KI
    # 期末最差是 CCC，且 CCC 全程未跌破 KI(60)，僅落在執行價(80)之下
    px.loc[sch.final_valuation] = [90.0, 95.0, 65.0]

    out = evaluate(terms, px, sch)

    assert out.ki_dates["AAA"] is not None
    assert out.ki_dates["CCC"] is None
    assert out.delivered_symbol == "CCC"
    assert any("並非觸發 KI" in w for w in out.warnings)


def test_ki_alone_does_not_trigger_delivery():
    """註記：觸發 KI 不等於立即承接，仍須看期末評價日。"""
    px, terms, sch = build()
    px.loc[sch.ki_days[10], :] = 50.0                   # 三檔同時破 KI
    px.loc[sch.final_valuation] = 95.0                  # 期末回到執行價之上

    out = evaluate(terms, px, sch)

    assert out.ki_occurred
    assert out.scenario is Scenario.MATURITY_ABOVE_STRIKE


def test_ki_level_must_be_below_strike():
    """註記：下限價需低於執行價格。"""
    with pytest.raises(ValueError, match="必須低於執行價"):
        base_terms(ki_pct=0.85, strike_pct=0.80)


# --------------------------------------------------------------------------
# 邊界條件：KO 用 >=、KI 用 <（嚴格）、執行價用 >=
# --------------------------------------------------------------------------


def test_ki_boundary_is_strict_less_than():
    px, terms, sch = build()
    px.loc[sch.ki_days[30], "AAA"] = 60.0               # 恰好等於 KI，不算觸發
    px.loc[sch.final_valuation] = 70.0

    out = evaluate(terms, px, sch)
    assert not out.ki_occurred
    assert out.scenario is Scenario.MATURITY_NO_KI

    px.loc[sch.ki_days[30], "AAA"] = 59.99              # 低一分錢就觸發
    out2 = evaluate(terms, px, sch)
    assert out2.ki_occurred
    assert out2.scenario is Scenario.DELIVERY


def test_strike_boundary_is_greater_or_equal():
    px, terms, sch = build()
    px.loc[sch.ki_days[30], "AAA"] = 55.0               # 觸發 KI
    px.loc[sch.final_valuation] = 80.0                  # 期末恰好等於執行價

    out = evaluate(terms, px, sch)
    assert out.scenario is Scenario.MATURITY_ABOVE_STRIKE

    px.loc[sch.final_valuation] = 79.99
    out2 = evaluate(terms, px, sch)
    assert out2.scenario is Scenario.DELIVERY


def test_ko_boundary_is_greater_or_equal():
    px, terms, sch = build()
    px.loc[sch.ko_days[10], :] = 99.99                  # 差一分錢不觸發
    assert evaluate(terms, px, sch).scenario is not Scenario.AUTOCALL

    px.loc[sch.ko_days[10], :] = 100.0                  # 等於即觸發
    assert evaluate(terms, px, sch).scenario is Scenario.AUTOCALL


def test_ko_window_excludes_lockout_and_final_valuation_date():
    """KO 觀察期 = [發行日+1M, 期末評價日)，兩端都要排除。"""
    px, terms, sch = build()

    assert sch.ko_days[0] >= sch.issue_date + pd.DateOffset(months=1)
    assert sch.ko_days[-1] < sch.final_valuation
    assert sch.final_valuation not in sch.ko_days

    # 鎖定期內衝到 KO 之上不算數
    early = px.index[(px.index > sch.trade_date) & (px.index < sch.ko_days[0])]
    px.loc[early, :] = 150.0
    px.loc[sch.final_valuation] = 90.0

    out = evaluate(terms, px, sch)
    assert out.scenario is not Scenario.AUTOCALL
    assert all(v is None for v in out.memory_dates.values())


# --------------------------------------------------------------------------
# 型式變化：EKI / 無 KI / 非記憶式 KO
# --------------------------------------------------------------------------


def test_eki_only_observes_final_valuation_date():
    terms = base_terms(ki_type=KIType.EKI)
    px, terms, sch = build(terms)
    px.loc[sch.ki_days[0] - pd.Timedelta(days=0)] = px.loc[sch.final_valuation]
    px.loc[px.index[30], "AAA"] = 30.0                  # 期中重挫，EKI 不看
    px.loc[sch.final_valuation] = 70.0                  # 期末 < 執行價但 > KI

    out = evaluate(terms, px, sch)
    assert not out.ki_occurred
    assert out.scenario is Scenario.MATURITY_NO_KI


def test_no_ki_condition_falls_back_to_strike_comparison():
    """註記：若無 KI 條件，直接以期末最差標的是否 < 執行價判定承接。"""
    terms = base_terms(ki_type=KIType.NONE, ki_pct=None)
    px, terms, sch = build(terms)
    px.loc[sch.final_valuation] = 70.0                  # 從未破 60，但無 KI 條件

    out = evaluate(terms, px, sch)
    assert out.scenario is Scenario.DELIVERY
    assert out.delivery_price == pytest.approx(80.0)


def test_non_memory_ko_requires_same_day():
    terms = base_terms(ko_type=KOType.DAILY)
    px, terms, sch = build(terms)
    d = sch.ko_days
    px.loc[d[10], "AAA"] = 101.0                        # 不同日觸及 -> 不成立
    px.loc[d[20], "BBB"] = 101.0
    px.loc[d[30], "CCC"] = 101.0
    px.loc[sch.final_valuation] = 90.0

    assert evaluate(terms, px, sch).scenario is not Scenario.AUTOCALL

    px.loc[d[40], :] = 101.0                            # 同日全數觸及 -> 成立
    out2 = evaluate(terms, px, sch)
    assert out2.scenario is Scenario.AUTOCALL
    assert out2.exit_date == d[40]


def test_full_period_coupon_mode():
    terms = base_terms(autocall_coupon=AutocallCoupon.FULL_PERIODS)
    px, terms, sch = build(terms)
    px.loc[sch.ko_days[30], :] = 101.0

    out = evaluate(terms, px, sch)
    n_paid = sum(1 for d in sch.coupon_dates if d <= out.exit_date)
    assert out.coupon_total == pytest.approx(n_paid * 1_000.0)
    assert out.coupon_cashflows[-1][0] <= out.exit_date


def test_fractional_share_is_cashed_at_final_close_not_strike():
    """條款：未足整股者「以期末評價日收盤價」折算現金，不是以執行價折算。

    面額 100,000 / 執行價 84（= 105 x 80%）= 1190.476... 股，必產生零股。
    """
    terms = base_terms(integer_shares=True)
    px, terms, sch = build(terms)
    px.iloc[0, px.columns.get_loc("CCC")] = 105.0        # CCC 期初 105 -> 執行價 84
    px.loc[sch.ki_days[30], "AAA"] = 55.0
    px.loc[sch.final_valuation] = [95.0, 95.0, 50.0]     # CCC 期末 50，表現最差

    out = evaluate(terms, px, sch)
    assert out.delivered_symbol == "CCC"
    assert out.delivery_price == pytest.approx(84.0)

    exact = 100_000 / 84.0
    assert out.shares == np.floor(exact)
    assert exact - out.shares > 0                        # 確實有零股
    # 零股以期末收盤 50 折現，而非以執行價 84
    assert out.residual_cash == pytest.approx((exact - out.shares) * 50.0)
    assert out.residual_cash != pytest.approx((exact - out.shares) * 84.0)


def test_integer_and_fractional_settlement_give_same_value():
    """零股以期末收盤價折現時，整股交割與零股交割的總價值完全相同。"""
    px, base, sch = build(base_terms())
    px.iloc[0, px.columns.get_loc("CCC")] = 105.0
    px.loc[sch.ki_days[30], "AAA"] = 55.0
    px.loc[sch.final_valuation] = [95.0, 95.0, 50.0]

    whole = evaluate(base_terms(integer_shares=True), px, sch)
    frac = evaluate(base_terms(integer_shares=False), px, sch)

    assert whole.shares != frac.shares
    assert whole.total_value == pytest.approx(frac.total_value)


def test_schedule_key_dates():
    px, terms, sch = build()
    days = px.index
    assert sch.trade_date == days[0]
    assert sch.issue_date == days[5]                    # I Delay = 5BD
    assert len(sch.coupon_dates) == 12
    assert sch.final_valuation <= sch.trade_date + pd.DateOffset(months=12)
