"""歷史回測：把同一組條款套用到歷史上每一個可能的進場日。

回答的問題是「這組條件如果在過去每一週都買一次，結果會怎樣」，
與 :func:`fcn.mc.forecast` 的前瞻模擬互為對照。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import MarketData
from .engine import FCNOutcome, Scenario, evaluate
from .schedule import build_schedule
from .terms import FCNTerms


@dataclass
class BacktestResult:
    terms: FCNTerms
    outcomes: list[FCNOutcome]

    def to_frame(self) -> pd.DataFrame:
        rows = []
        for o in self.outcomes:
            rows.append(
                {
                    "交易日": o.schedule.trade_date.date(),
                    "出場日": o.exit_date.date(),
                    "情境": o.scenario.value,
                    "持有天數": o.days_held,
                    "配息": o.coupon_total,
                    "總價值": o.total_value,
                    "報酬率": o.return_pct,
                    "年化報酬": o.annualized_pct,
                    "最差標的": o.worst_symbol,
                    "最差表現": o.worst_performance - 1.0,
                    "曾觸KI": o.ki_occurred,
                    "承接標的": o.delivered_symbol or "",
                }
            )
        return pd.DataFrame(rows)

    def summary(self) -> dict[str, float | int | str]:
        df = self.to_frame()
        if df.empty:
            return {"樣本數": 0}
        r = df["報酬率"].to_numpy()
        bh = df["最差表現"].to_numpy()
        counts = df["情境"].value_counts()
        n = len(df)
        return {
            "樣本數": n,
            "平均報酬": float(r.mean()),
            "中位數報酬": float(np.median(r)),
            "勝率": float((r > 0).mean()),
            "最差報酬": float(r.min()),
            "最佳報酬": float(r.max()),
            "5% VaR": float(np.percentile(r, 5)),
            "提前出場比例": float(counts.get(Scenario.AUTOCALL.value, 0) / n),
            "到期還本比例": float(
                (
                    counts.get(Scenario.MATURITY_NO_KI.value, 0)
                    + counts.get(Scenario.MATURITY_ABOVE_STRIKE.value, 0)
                )
                / n
            ),
            "承接股票比例": float(counts.get(Scenario.DELIVERY.value, 0) / n),
            "曾觸KI比例": float(df["曾觸KI"].mean()),
            "平均持有天數": float(df["持有天數"].mean()),
            "對照:直接持有最差標的平均報酬": float(bh.mean()),
            "勝過直接持有比例": float((r > bh).mean()),
        }

    def scenario_breakdown(self) -> pd.DataFrame:
        df = self.to_frame()
        if df.empty:
            return df
        g = df.groupby("情境")["報酬率"]
        out = pd.DataFrame(
            {
                "次數": g.size(),
                "占比": g.size() / len(df),
                "平均報酬": g.mean(),
                "最差": g.min(),
                "最佳": g.max(),
            }
        )
        return out.sort_values("次數", ascending=False)


def run_backtest(
    terms: FCNTerms,
    md: MarketData,
    *,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    step_days: int = 5,
) -> BacktestResult:
    """對歷史上每隔 ``step_days`` 個交易日的進場點各發行一檔，回測到期結果。

    只有「完整走完契約期間」的進場日才會被納入（避免存活者偏誤下的半段樣本）。
    """
    if terms.coupon_pa is None:
        raise ValueError("回測需要 terms.coupon_pa；請先用 mc.price() 取得公允配息率")

    days = md.trading_days
    lo = days[0] if start is None else max(days[0], pd.Timestamp(start))
    hi = days[-1] if end is None else min(days[-1], pd.Timestamp(end))
    candidates = days[(days >= lo) & (days <= hi)][::step_days]

    outcomes: list[FCNOutcome] = []
    for td in candidates:
        try:
            sch = build_schedule(terms, days, td)
        except ValueError:
            break  # 剩餘資料已不足一個完整契約期間
        if sch.final_valuation > days[-1]:
            break
        outcomes.append(evaluate(terms, md.closes, sch))

    return BacktestResult(terms=terms, outcomes=outcomes)
