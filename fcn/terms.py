"""FCN 報價條件（Term Sheet）定義。

欄位對應永豐金證券報價條件表：
Product / UD1~UD4 / CCY / Tenor(M) / Cpn p.a. / I Delay /
Cpn Frequency / Put Strike / KO Type / Autocall / KI Type / KI Level
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class KOType(str, Enum):
    """提前出場（Autocall）觀察型式。"""

    DAILY_MEMORY = "Daily Memory"      # 每日觀察 + 記憶式
    DAILY = "Daily"                    # 每日觀察，所有標的須「同日」皆 >= KO
    MONTHLY_MEMORY = "Monthly Memory"  # 每月觀察日 + 記憶式
    MONTHLY = "Monthly"                # 每月觀察日，須「同日」皆 >= KO
    NONE = "None"                      # 無提前出場機制

    @property
    def has_memory(self) -> bool:
        return self in (KOType.DAILY_MEMORY, KOType.MONTHLY_MEMORY)

    @property
    def is_monthly(self) -> bool:
        return self in (KOType.MONTHLY_MEMORY, KOType.MONTHLY)


class KIType(str, Enum):
    """觸及生效（Knock-In）觀察型式。"""

    AKI = "AKI"    # 美式：自交易日(含)起至期末評價日(含)每一預定交易日
    EKI = "EKI"    # 歐式：僅期末評價日
    NONE = "None"  # 無 KI 條件 -> 直接以期末最差表現標的是否 < 執行價判定承接


class CouponFreq(str, Enum):
    MONTHLY = "Monthly"
    QUARTERLY = "Quarterly"
    SEMIANNUAL = "Semiannual"
    ANNUAL = "Annual"

    @property
    def months(self) -> int:
        return {"Monthly": 1, "Quarterly": 3, "Semiannual": 6, "Annual": 12}[self.value]

    @property
    def per_year(self) -> int:
        return 12 // self.months


class AutocallCoupon(str, Enum):
    """提前出場時的配息計算方式。"""

    ACCRUED = "accrued"            # 按實際天數累計計息（ACT/365）
    FULL_PERIODS = "full_periods"  # 僅已完整經過的配息期數


@dataclass
class FCNTerms:
    """一檔 FCN 的完整條款。

    百分比欄位一律以「小數」表示：80% -> 0.80。
    """

    underlyings: list[str]                                  # UD1~UD4，1~4 檔
    tenor_months: int = 12                                  # Tenor (M)
    coupon_pa: float | None = None                          # Cpn p.a.；None = 由模型反推
    currency: str = "USD"                                   # CCY
    issue_delay_bd: int = 5                                 # I Delay (5BD)
    coupon_freq: CouponFreq = CouponFreq.MONTHLY             # Cpn Frequency
    strike_pct: float = 0.80                                # Put Strike
    ko_type: KOType = KOType.DAILY_MEMORY                   # KO Type
    autocall_pct: float = 1.00                              # Autocall 提前出場價
    ki_type: KIType = KIType.AKI                            # KI Type
    ki_pct: float | None = 0.60                             # KI Level 下限價

    # --- Upside FCN 專屬：可參與漲幅 ---
    lower_call_strike_pct: float | None = None              # Lower Call Strike 參與表現價
    participation: float = 1.00                             # 參與率（範例為 100%）

    # --- 非報價欄位，模型設定 ---
    notional: float = 100_000.0                             # 面額
    ko_lockout_months: int = 1                              # 發行日後幾個月才開始 KO 觀察
    tenor_from: str = "trade"                               # "trade" | "issue"
    autocall_coupon: AutocallCoupon = AutocallCoupon.ACCRUED
    integer_shares: bool = True                             # 承接股票是否只交割整股 + 現金找補

    label: str = field(default="FCN")

    def __post_init__(self) -> None:
        if not 1 <= len(self.underlyings) <= 4:
            raise ValueError("連結標的須為 1~4 檔（UD1~UD4）")
        if len(set(self.underlyings)) != len(self.underlyings):
            raise ValueError("連結標的不可重複")
        if self.tenor_months <= 0:
            raise ValueError("天期須大於 0")
        if self.tenor_months % self.coupon_freq.months != 0:
            raise ValueError(
                f"天期 {self.tenor_months}M 無法被配息頻率 {self.coupon_freq.value} 整除"
            )
        if not 0 < self.strike_pct <= 2:
            raise ValueError("執行價須介於 0~200%")
        if self.ki_type is not KIType.NONE:
            if self.ki_pct is None:
                raise ValueError(f"KI Type = {self.ki_type.value} 但未設定 KI Level")
            # 永豐金註記：下限價需低於執行價格
            if self.ki_pct >= self.strike_pct:
                raise ValueError(
                    f"下限價 ({self.ki_pct:.2%}) 必須低於執行價 ({self.strike_pct:.2%})"
                )
        if self.ko_type is not KOType.NONE and self.autocall_pct <= 0:
            raise ValueError("提前出場價須大於 0")
        if self.tenor_from not in ("trade", "issue"):
            raise ValueError("tenor_from 僅接受 'trade' 或 'issue'")
        if self.lower_call_strike_pct is not None:
            if self.lower_call_strike_pct <= 0:
                raise ValueError("參與表現價須大於 0")
            if self.lower_call_strike_pct <= self.strike_pct:
                raise ValueError(
                    f"參與表現價 ({self.lower_call_strike_pct:.2%}) 須高於執行價 "
                    f"({self.strike_pct:.2%})"
                )
            if self.participation <= 0:
                raise ValueError("參與率須大於 0")

    @property
    def is_upside(self) -> bool:
        """是否為可參與漲幅型（Upside FCN）。"""
        return self.lower_call_strike_pct is not None

    @property
    def product_name(self) -> str:
        return "Upside FCN" if self.is_upside else "FCN"

    def upside_payment(self, worst_performance: float) -> float:
        """依最差表現標的計算參與漲幅金額。

        面額 x 參與率 x 最大值(0%, 最差表現之連結標的表現 - 參與表現價百分比)
        """
        if not self.is_upside:
            return 0.0
        excess = max(0.0, worst_performance - self.lower_call_strike_pct)
        return self.notional * self.participation * excess

    @property
    def n_coupons(self) -> int:
        return self.tenor_months // self.coupon_freq.months

    @property
    def coupon_per_period(self) -> float | None:
        """每期固定配息金額（coupon_pa 為 None 時回傳 None）。"""
        if self.coupon_pa is None:
            return None
        return self.notional * self.coupon_pa / self.coupon_freq.per_year

    def with_coupon(self, coupon_pa: float) -> "FCNTerms":
        """回傳一份填入指定年化配息的複本。"""
        from dataclasses import replace

        return replace(self, coupon_pa=coupon_pa)

    def describe(self) -> str:
        """還原成報價條件表的一行文字。"""
        uds = " / ".join(self.underlyings)
        cpn = "（待報價）" if self.coupon_pa is None else f"{self.coupon_pa:.2%}"
        ki = "—" if self.ki_type is KIType.NONE else f"{self.ki_pct:.0%}"
        upside = (
            f" | Lower Call {self.lower_call_strike_pct:.0%} @ {self.participation:.0%}"
            if self.is_upside
            else ""
        )
        return (
            f"{self.product_name} | {uds} | {self.currency} | {self.tenor_months}M | "
            f"Cpn {cpn} p.a. | I Delay {self.issue_delay_bd}BD | {self.coupon_freq.value} | "
            f"Strike {self.strike_pct:.0%} | KO {self.ko_type.value} @ {self.autocall_pct:.0%} | "
            f"KI {self.ki_type.value} @ {ki}{upside}"
        )
