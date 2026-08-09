"""FCN（固定配息提前出場境外結構型商品）報價、模擬與回測工具。"""

from .terms import FCNTerms, KOType, KIType, CouponFreq, AutocallCoupon
from .schedule import Schedule, build_schedule
from .engine import FCNOutcome, Scenario, evaluate

__all__ = [
    "FCNTerms", "KOType", "KIType", "CouponFreq", "AutocallCoupon",
    "Schedule", "build_schedule",
    "FCNOutcome", "Scenario", "evaluate",
]
