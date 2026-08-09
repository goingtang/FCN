"""FCN 給付規則引擎（單一路徑，參考實作）。

判定順序完全對應永豐金證券「運作機制」流程圖：

    ① 記憶事件   ── KO 觀察期間內，任一標的收盤價 >= 提前出場價 -> 該標的記憶
    ② 提前出場   ── 所有標的皆已記憶的當日 -> 領回 100% 本金 + 應計配息
    ③ 觸及生效   ── KI 觀察期間內，任一標的收盤價 <  下限價 -> 發生 KI 事件
                    未發生 -> 到期領回 100% 本金 + 全額配息
    ④ 期末比價   ── 已發生 KI，但期末所有標的收盤價 >= 執行價 -> 領回 100% 本金
    ⑤ 承接股票   ── 否則以執行價承接【表現最差標的】
                    股數 = 面額 / (最差標的期初價 x 執行價%)

四項條款註記（皆已落實於程式）
- 記憶事件與 KI 事件互為獨立：曾記憶之標的，到期若為最差且低於執行價仍須承接。
- 承接標的為「表現最差」者，不必然是觸發 KI 的那一檔。
- 觸發 KI 不等於立即承接，仍須看期末評價日收盤價。
- 邊界不對稱：KO 用 ``>=``、KI 用 ``<``（嚴格小於）、執行價用 ``>=``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from .schedule import Schedule
from .terms import AutocallCoupon, FCNTerms, KIType, KOType


class Scenario(str, Enum):
    AUTOCALL = "提前出場"
    MATURITY_NO_KI = "到期還本（未觸及生效）"
    MATURITY_ABOVE_STRIKE = "到期還本（期末 >= 執行價）"
    DELIVERY = "到期承接股票"

    @property
    def returns_principal(self) -> bool:
        return self is not Scenario.DELIVERY


@dataclass
class FCNOutcome:
    """單一路徑的完整給付結果。"""

    scenario: Scenario
    terms: FCNTerms
    schedule: Schedule

    initial_prices: pd.Series
    final_prices: pd.Series              # 期末評價日收盤（提前出場時為出場日收盤）
    ko_levels: pd.Series
    ki_levels: pd.Series | None
    strike_levels: pd.Series

    exit_date: pd.Timestamp
    days_held: int
    memory_dates: dict[str, pd.Timestamp | None]
    ki_dates: dict[str, pd.Timestamp | None]

    coupon_cashflows: list[tuple[pd.Timestamp, float]]
    coupon_total: float
    principal_returned: float

    worst_symbol: str
    worst_performance: float             # 期末收盤 / 期初收盤
    performances: pd.Series

    delivered_symbol: str | None = None
    delivery_price: float = 0.0
    shares: float = 0.0
    stock_value: float = 0.0
    residual_cash: float = 0.0
    upside_payment: float = 0.0          # Upside FCN：參與最差標的漲幅的給付

    warnings: list[str] = field(default_factory=list)

    # ---- 衍生數字 ----

    @property
    def total_value(self) -> float:
        """出場時投資人實際持有的總價值（含股票以期末收盤計）。"""
        return (
            self.principal_returned
            + self.stock_value
            + self.residual_cash
            + self.upside_payment
            + self.coupon_total
        )

    @property
    def pnl(self) -> float:
        return self.total_value - self.terms.notional

    @property
    def return_pct(self) -> float:
        return self.pnl / self.terms.notional

    @property
    def annualized_pct(self) -> float:
        yrs = self.days_held / 365.0
        if yrs <= 0:
            return 0.0
        return (1.0 + self.return_pct) ** (1.0 / yrs) - 1.0

    @property
    def ki_occurred(self) -> bool:
        return any(d is not None for d in self.ki_dates.values())

    def buy_and_hold_return(self) -> float:
        """同期直接買進「表現最差標的」的報酬（機會成本對照）。"""
        return self.worst_performance - 1.0


def _accrued_coupon(
    terms: FCNTerms, schedule: Schedule, exit_date: pd.Timestamp
) -> list[tuple[pd.Timestamp, float]]:
    """提前出場時的配息現金流。"""
    per = terms.coupon_per_period
    if per is None:
        raise ValueError("terms.coupon_pa 未設定，無法計算配息")

    paid = [(d, per) for d in schedule.coupon_dates if d <= exit_date]
    if terms.autocall_coupon is AutocallCoupon.FULL_PERIODS:
        return paid

    # ACT/365 累計計息，扣掉已配發的完整期數
    total = terms.notional * terms.coupon_pa * (exit_date - schedule.issue_date).days / 365.0
    stub = total - sum(a for _, a in paid)
    if stub > 1e-12:
        paid = paid + [(exit_date, stub)]
    return paid


def evaluate(terms: FCNTerms, closes: pd.DataFrame, schedule: Schedule) -> FCNOutcome:
    """對一條實際（或模擬）價格路徑求算 FCN 給付結果。

    ``closes`` 需為 index=交易日、columns=標的的未還原股息收盤價，且涵蓋
    ``schedule.trade_date`` ~ ``schedule.final_valuation``。
    """
    if terms.coupon_pa is None:
        raise ValueError("terms.coupon_pa 未設定；請先用 pricing.solve_fair_coupon() 求解")

    syms = list(closes.columns)
    warnings: list[str] = []

    initial = closes.loc[schedule.trade_date].astype(float)
    ko_levels = initial * terms.autocall_pct
    strike_levels = initial * terms.strike_pct
    ki_levels = initial * terms.ki_pct if terms.ki_type is not KIType.NONE else None

    # ---------- ① 記憶事件 / ② 提前出場 ----------
    memory_dates: dict[str, pd.Timestamp | None] = {s: None for s in syms}
    autocall_date: pd.Timestamp | None = None

    if terms.ko_type is not KOType.NONE and len(schedule.ko_days) > 0:
        window = closes.loc[schedule.ko_days]
        hit = (window.to_numpy(dtype=float) >= ko_levels.to_numpy(dtype=float)[None, :])

        for j, s in enumerate(syms):
            idx = np.flatnonzero(hit[:, j])
            if idx.size:
                memory_dates[s] = schedule.ko_days[idx[0]]

        state = np.maximum.accumulate(hit, axis=0) if terms.ko_type.has_memory else hit
        all_hit = np.flatnonzero(state.all(axis=1))
        if all_hit.size:
            autocall_date = schedule.ko_days[all_hit[0]]

    # ---------- ③ 觸及生效 ----------
    ki_dates: dict[str, pd.Timestamp | None] = {s: None for s in syms}
    if ki_levels is not None and len(schedule.ki_days) > 0:
        kwin = closes.loc[schedule.ki_days]
        breach = kwin.to_numpy(dtype=float) < ki_levels.to_numpy(dtype=float)[None, :]
        for j, s in enumerate(syms):
            idx = np.flatnonzero(breach[:, j])
            if idx.size:
                ki_dates[s] = schedule.ki_days[idx[0]]

    # 無 KI 條件者，視同已觸發，直接以期末收盤 vs 執行價判定（條款註記 ⑤）
    ki_occurred = (
        True if terms.ki_type is KIType.NONE else any(d is not None for d in ki_dates.values())
    )

    final_all = closes.loc[schedule.final_valuation].astype(float)
    performances = (final_all / initial).astype(float)
    worst_symbol = str(performances.idxmin())
    worst_performance = float(performances.min())

    # ---------- 提前出場分支 ----------
    if autocall_date is not None:
        cashflows = _accrued_coupon(terms, schedule, autocall_date)
        exit_prices = closes.loc[autocall_date].astype(float)
        exit_perf = (exit_prices / initial).astype(float)
        return FCNOutcome(
            scenario=Scenario.AUTOCALL,
            terms=terms,
            schedule=schedule,
            initial_prices=initial,
            final_prices=exit_prices,
            ko_levels=ko_levels,
            ki_levels=ki_levels,
            strike_levels=strike_levels,
            exit_date=autocall_date,
            days_held=int((autocall_date - schedule.issue_date).days),
            memory_dates=memory_dates,
            ki_dates=ki_dates,
            coupon_cashflows=cashflows,
            coupon_total=float(sum(a for _, a in cashflows)),
            principal_returned=terms.notional,
            upside_payment=terms.upside_payment(float(exit_perf.min())),
            worst_symbol=str(exit_perf.idxmin()),
            worst_performance=float(exit_perf.min()),
            performances=exit_perf,
            warnings=warnings,
        )

    # ---------- 到期分支 ----------
    per = terms.coupon_per_period
    cashflows = [(d, per) for d in schedule.coupon_dates]
    coupon_total = float(sum(a for _, a in cashflows))
    days_held = int((schedule.final_valuation - schedule.issue_date).days)

    common = dict(
        terms=terms,
        schedule=schedule,
        initial_prices=initial,
        final_prices=final_all,
        ko_levels=ko_levels,
        ki_levels=ki_levels,
        strike_levels=strike_levels,
        exit_date=schedule.final_valuation,
        days_held=days_held,
        memory_dates=memory_dates,
        ki_dates=ki_dates,
        coupon_cashflows=cashflows,
        coupon_total=coupon_total,
        worst_symbol=worst_symbol,
        worst_performance=worst_performance,
        performances=performances,
        warnings=warnings,
    )

    # Upside FCN：所有「還本」情境皆參與最差標的相對參與表現價的漲幅
    upside = terms.upside_payment(worst_performance)

    if not ki_occurred:
        return FCNOutcome(
            scenario=Scenario.MATURITY_NO_KI,
            principal_returned=terms.notional,
            upside_payment=upside,
            **common,
        )

    # ④ 已觸及生效，但期末所有標的皆 >= 執行價 -> 仍全額還本
    if bool((final_all.to_numpy() >= strike_levels.to_numpy()).all()):
        return FCNOutcome(
            scenario=Scenario.MATURITY_ABOVE_STRIKE,
            principal_returned=terms.notional,
            upside_payment=upside,
            **common,
        )

    # ⑤ 承接表現最差標的
    delivery_price = float(initial[worst_symbol] * terms.strike_pct)
    final_worst = float(final_all[worst_symbol])
    exact = terms.notional / delivery_price
    if terms.integer_shares:
        # 條款：交付整股（面額 / 執行價），未足整股者以期末評價日收盤價折算現金
        shares = float(np.floor(exact))
        residual = float((exact - shares) * final_worst)
    else:
        shares = float(exact)
        residual = 0.0

    if memory_dates.get(worst_symbol) is not None:
        warnings.append(
            f"{worst_symbol} 曾發生記憶事件，仍因期末為最差表現標的且低於執行價而被承接"
            "（記憶事件與觸及生效事件互為獨立）"
        )
    if ki_dates.get(worst_symbol) is None and terms.ki_type is not KIType.NONE:
        trig = [s for s, d in ki_dates.items() if d is not None]
        warnings.append(
            f"承接標的 {worst_symbol} 並非觸發 KI 的標的（觸發者：{', '.join(trig)}）"
        )

    return FCNOutcome(
        scenario=Scenario.DELIVERY,
        principal_returned=0.0,
        delivered_symbol=worst_symbol,
        delivery_price=delivery_price,
        shares=shares,
        stock_value=float(shares * final_worst),
        residual_cash=residual,
        **common,
    )
