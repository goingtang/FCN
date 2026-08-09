"""美股（NYSE/Nasdaq）交易日曆。

模擬未來路徑時，障礙觀察日的「數量」會直接影響觸及機率，因此不能用
單純的週一至週五近似（每年約 261 天 vs 實際約 252 天）。此處實作 NYSE
的固定假期規則。
"""

from __future__ import annotations

import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
)
from pandas.tseries.offsets import CustomBusinessDay


class NYSECalendar(AbstractHolidayCalendar):
    """NYSE 固定假期（不含臨時休市與提早收盤）。"""

    rules = [
        Holiday("New Years Day", month=1, day=1, observance=nearest_workday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-06-20",
                observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


_BDAY = CustomBusinessDay(calendar=NYSECalendar())


def trading_days(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """回傳 ``[start, end]`` 之間的 NYSE 交易日。"""
    return pd.DatetimeIndex(
        pd.date_range(start=start, end=end, freq=_BDAY), name="date"
    )


def future_trading_days(start: pd.Timestamp, months: int, pad_days: int = 15) -> pd.DatetimeIndex:
    """自 ``start`` 起，涵蓋 ``months`` 個月（含緩衝）的交易日。"""
    end = pd.Timestamp(start) + pd.DateOffset(months=months) + pd.Timedelta(days=pad_days)
    return trading_days(pd.Timestamp(start), end)
