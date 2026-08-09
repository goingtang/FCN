"""契約日程：交易日、發行日、KO/KI 觀察期間、配息日、期末評價日。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .terms import FCNTerms, KIType, KOType


def _on_or_after(days: pd.DatetimeIndex, target: pd.Timestamp) -> pd.Timestamp:
    pos = days.searchsorted(target, side="left")
    if pos >= len(days):
        raise ValueError(f"行情資料不足：找不到 {target.date()} 當日或之後的交易日")
    return days[pos]


def _on_or_before(days: pd.DatetimeIndex, target: pd.Timestamp) -> pd.Timestamp:
    pos = days.searchsorted(target, side="right") - 1
    if pos < 0:
        raise ValueError(f"行情資料不足：找不到 {target.date()} 當日或之前的交易日")
    return days[pos]


def _shift_trading_days(days: pd.DatetimeIndex, start: pd.Timestamp, n: int) -> pd.Timestamp:
    pos = days.searchsorted(start, side="left") + n
    if pos >= len(days):
        raise ValueError(f"行情資料不足：{start.date()} 之後沒有 {n} 個交易日")
    return days[pos]


@dataclass
class Schedule:
    """單一筆 FCN 的完整日程。"""

    trade_date: pd.Timestamp          # 交易日（期初定價日）
    issue_date: pd.Timestamp          # 發行日 = 交易日 + I Delay 個營業日
    final_valuation: pd.Timestamp     # 期末評價日
    ko_days: pd.DatetimeIndex         # 提前出場觀察日（不含期末評價日）
    ki_days: pd.DatetimeIndex         # 觸及生效觀察日
    coupon_dates: list[pd.Timestamp]  # 各期配息日

    @property
    def tenor_days(self) -> int:
        return int((self.final_valuation - self.issue_date).days)


def build_schedule(
    terms: FCNTerms, trading_days: pd.DatetimeIndex, trade_date: str | pd.Timestamp
) -> Schedule:
    """依條款與市場交易日曆推出完整日程。

    - 交易日：使用者指定日（非交易日則順延至下一個交易日），當日收盤價為期初價。
    - 發行日：交易日 + ``I Delay`` 個交易日。
    - 期末評價日：自 ``tenor_from``（預設交易日）起算 ``Tenor`` 個月，
      落在非交易日時順延（Following）。
    - KO 觀察期間：自「發行日 + ``ko_lockout_months`` 個月」起，至期末評價日**前**
      （不含期末評價日）。
    - KI 觀察期間：AKI = 交易日(含) ~ 期末評價日(含)；EKI = 僅期末評價日。
    """
    days = pd.DatetimeIndex(trading_days)
    td = _on_or_after(days, pd.Timestamp(trade_date))
    issue = _shift_trading_days(days, td, terms.issue_delay_bd)

    anchor = td if terms.tenor_from == "trade" else issue
    fvd_target = anchor + pd.DateOffset(months=terms.tenor_months)
    fvd = _on_or_before(days, fvd_target)
    if fvd <= issue:
        raise ValueError("期末評價日不得早於發行日，請確認天期與行情區間")

    if terms.ko_type is KOType.NONE:
        ko_days = days[:0]
    else:
        ko_start_target = issue + pd.DateOffset(months=terms.ko_lockout_months)
        ko_days = days[(days >= ko_start_target) & (days < fvd)]
        if terms.ko_type.is_monthly:
            # 每月觀察：自 KO 起算日起，每隔一個月取當日或之後最近的交易日
            picks: list[pd.Timestamp] = []
            k = 0
            while True:
                target = ko_start_target + pd.DateOffset(months=k)
                if target >= fvd:
                    break
                pos = days.searchsorted(target, side="left")
                if pos >= len(days) or days[pos] >= fvd:
                    break
                picks.append(days[pos])
                k += 1
            ko_days = pd.DatetimeIndex(sorted(set(picks)))

    if terms.ki_type is KIType.AKI:
        ki_days = days[(days >= td) & (days <= fvd)]
    elif terms.ki_type is KIType.EKI:
        ki_days = pd.DatetimeIndex([fvd])
    else:
        ki_days = days[:0]

    step = terms.coupon_freq.months
    coupon_dates = [
        issue + pd.DateOffset(months=step * k) for k in range(1, terms.n_coupons + 1)
    ]

    return Schedule(
        trade_date=td,
        issue_date=issue,
        final_valuation=fvd,
        ko_days=ko_days,
        ki_days=ki_days,
        coupon_dates=coupon_dates,
    )
