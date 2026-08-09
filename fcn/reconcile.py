"""以真實 Term Sheet 對帳。

輸入一份條款確認書的內容（條款 + 文件載明的絕對價位 + 已知的實際結果），
用實際行情跑一次引擎，逐項比對模型結果與文件記載。

股票分割的陷阱
--------------
Yahoo 的收盤價序列**已還原分割**，但 Term Sheet 上的價位是當時的原始報價。
例如 2021 年發行、連結 NVDA 的商品會寫「期初價 309.40」，而 NVDA 在 2024 年
6 月 1 拆 10 之後，Yahoo 回傳的同一天收盤價是 30.94。若直接把文件價位套上
Yahoo 序列，判定會整個歪掉。本模組會偵測這個比例並自動換算（可關閉）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .data import MarketData, load_market_data
from .engine import BarrierLevels, FCNOutcome, Scenario, evaluate
from .schedule import Schedule, build_schedule
from .terms import AutocallCoupon, CouponFreq, FCNTerms, KIType, KOType

_KO_BY_VALUE = {k.value.lower(): k for k in KOType}
_KI_BY_VALUE = {k.value.lower(): k for k in KIType} | {"na": KIType.NONE, "none": KIType.NONE}
_FREQ_BY_VALUE = {k.value.lower(): k for k in CouponFreq}


@dataclass
class TermSheet:
    """一份條款確認書的可對帳內容。"""

    terms: FCNTerms
    trade_date: pd.Timestamp
    levels: BarrierLevels

    # 文件或對帳單載明的實際結果（可選，用於逐項驗證）
    expected_scenario: str | None = None
    expected_exit_date: str | None = None
    expected_coupon_total: float | None = None
    expected_total_value: float | None = None
    expected_delivered_symbol: str | None = None
    expected_shares: float | None = None
    expected_delivery_price: float | None = None

    # 覆寫日程（若文件明列，優先於推算值）
    issue_date: str | None = None
    final_valuation: str | None = None

    label: str = "Term Sheet"

    @staticmethod
    def from_dict(d: dict) -> "TermSheet":
        t = d.get("terms", d)
        ki_type = _KI_BY_VALUE.get(str(t.get("ki_type", "AKI")).lower())
        if ki_type is None:
            raise ValueError(f"未知的 KI Type：{t.get('ki_type')!r}")
        ko_type = _KO_BY_VALUE.get(str(t.get("ko_type", "Daily Memory")).lower())
        if ko_type is None:
            raise ValueError(f"未知的 KO Type：{t.get('ko_type')!r}")
        freq = _FREQ_BY_VALUE.get(str(t.get("coupon_freq", "Monthly")).lower())
        if freq is None:
            raise ValueError(f"未知的配息頻率：{t.get('coupon_freq')!r}")

        p = lambda k, dflt=None: (  # noqa: E731 - 百分比欄位以 % 為單位輸入
            None if t.get(k) is None else float(t[k]) / 100.0
        ) if t.get(k) is not None else dflt

        terms = FCNTerms(
            underlyings=list(t["underlyings"]),
            currency=t.get("currency", "USD"),
            tenor_months=int(t["tenor_months"]),
            coupon_pa=p("coupon_pa"),
            issue_delay_bd=int(t.get("issue_delay_bd", 5)),
            coupon_freq=freq,
            strike_pct=p("strike_pct", 0.80),
            ko_type=ko_type,
            autocall_pct=p("autocall_pct", 1.00),
            ki_type=ki_type,
            ki_pct=None if ki_type is KIType.NONE else p("ki_pct", 0.60),
            lower_call_strike_pct=p("lower_call_strike_pct"),
            participation=p("participation", 1.0) or 1.0,
            rebate=p("rebate", 0.0) or 0.0,
            notional=float(t.get("notional", 100_000)),
            ko_lockout_months=int(t.get("ko_lockout_months", 1)),
            integer_shares=bool(t.get("integer_shares", True)),
            autocall_coupon=(
                AutocallCoupon.FULL_PERIODS
                if t.get("autocall_coupon") == "full_periods"
                else AutocallCoupon.ACCRUED
            ),
        )

        lv = d.get("levels", {})
        levels = BarrierLevels(
            initial={k: float(v) for k, v in lv["initial"].items()},
            ko=None if lv.get("ko") is None else {k: float(v) for k, v in lv["ko"].items()},
            strike=(
                None if lv.get("strike") is None
                else {k: float(v) for k, v in lv["strike"].items()}
            ),
            ki=None if lv.get("ki") is None else {k: float(v) for k, v in lv["ki"].items()},
        )

        e = d.get("expected", {})
        return TermSheet(
            terms=terms,
            trade_date=pd.Timestamp(d["trade_date"]),
            levels=levels,
            expected_scenario=e.get("scenario"),
            expected_exit_date=e.get("exit_date"),
            expected_coupon_total=e.get("coupon_total"),
            expected_total_value=e.get("total_value"),
            expected_delivered_symbol=e.get("delivered_symbol"),
            expected_shares=e.get("shares"),
            expected_delivery_price=e.get("delivery_price"),
            issue_date=d.get("issue_date"),
            final_valuation=d.get("final_valuation"),
            label=d.get("label", "Term Sheet"),
        )

    @staticmethod
    def from_json(path: str | Path) -> "TermSheet":
        return TermSheet.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class Check:
    """單一比對項目。"""

    item: str
    expected: str
    actual: str
    ok: bool | None          # None = 文件未提供，無從比對
    note: str = ""

    @property
    def mark(self) -> str:
        return {True: "PASS", False: "FAIL", None: "  — "}[self.ok]


@dataclass
class Reconciliation:
    sheet: TermSheet
    outcome: FCNOutcome
    schedule: Schedule
    checks: list[Check]
    split_ratios: dict[str, float]
    notes: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]

    @property
    def compared(self) -> list[Check]:
        return [c for c in self.checks if c.ok is not None]

    @property
    def passed(self) -> bool:
        return not self.failures


def _approx(a: float | None, b: float | None, tol: float, rel: bool = False) -> bool | None:
    if a is None or b is None:
        return None
    if rel:
        scale = max(abs(a), abs(b), 1e-12)
        return abs(a - b) / scale <= tol
    return abs(a - b) <= tol


def detect_split_ratios(
    initial: dict[str, float], closes: pd.DataFrame, trade_date: pd.Timestamp
) -> dict[str, float]:
    """比較文件期初價與行情收盤價，推得各標的的分割/合併倍數。

    回傳「文件價位 ÷ 行情價位」。1.0 表示兩者一致；10.0 表示行情已還原
    1 拆 10，需把行情序列乘以 10 才會回到文件當時的價格尺度。
    """
    out: dict[str, float] = {}
    for sym, stated in initial.items():
        market = float(closes[sym].loc[trade_date])
        out[sym] = float(stated) / market if market else float("nan")
    return out


# 實務上出現過的分割／反分割比例。刻意不用一般的有理數近似：
# limit_denominator 會把 1.037 近似成 28/27，等於把 3.7% 的價格錯誤
# 當成「乾淨的分割倍數」吸收掉，正好放過我們最想抓到的那種錯誤。
_SPLIT_CANDIDATES = sorted(
    {1.0}
    | {float(n) for n in range(2, 101)}
    | {1.0 / n for n in range(2, 101)}
    | {p / q for p, q in
       [(3, 2), (5, 4), (5, 2), (7, 2), (4, 3), (5, 3), (7, 5), (8, 5), (10, 3), (7, 4)]}
    | {q / p for p, q in
       [(3, 2), (5, 4), (5, 2), (7, 2), (4, 3), (5, 3), (7, 5), (8, 5), (10, 3), (7, 4)]}
)


def nearest_clean_ratio(r: float) -> float:
    """把偵測到的倍數收斂到最接近的實際分割比例（1、10、1/3、3/2 …）。

    找不到夠接近的候選時回傳 1.0，讓後續的期初價比對把差異暴露出來。
    """
    if not (r and r == r and 0 < abs(r) < 1e6):
        return 1.0
    return min(_SPLIT_CANDIDATES, key=lambda c: abs(r - c) / c)


def _rescale_prices(closes: pd.DataFrame, ratios: dict[str, float]) -> pd.DataFrame:
    """把行情序列還原成 Term Sheet 當時的價格尺度。

    必須調整行情而非文件價位：實體交割的整股數是在原始價格上取整的，
    100,000 / 247.60 取整為 403 股，與 100,000 / 24.76 取整的 4,039 股
    並非 10 倍關係，換算方向錯了會算出不同的零股找補與總價值。
    """
    factors = pd.Series({c: ratios.get(c, 1.0) for c in closes.columns}, dtype=float)
    return closes.mul(factors, axis=1)


def reconcile(
    sheet: TermSheet,
    md: MarketData | None = None,
    *,
    auto_adjust_splits: bool = True,
    split_tolerance: float = 0.02,
    price_tol: float = 0.01,
    money_tol: float = 1.0,
) -> Reconciliation:
    """用實際行情重跑 Term Sheet，逐項比對。"""
    terms = sheet.terms
    if md is None:
        end = min(
            sheet.trade_date + pd.DateOffset(months=terms.tenor_months) + pd.Timedelta(days=45),
            pd.Timestamp.today().normalize(),
        )
        md = load_market_data(terms.underlyings, sheet.trade_date - pd.Timedelta(days=10), end)

    ysyms = [md.symbol_map[u] for u in terms.underlyings]
    closes = md.closes[ysyms]

    sch = build_schedule(terms, md.trading_days, sheet.trade_date)
    notes: list[str] = []

    # 文件上的期初價以 Yahoo 代碼或原始代碼標記皆可
    norm_initial = {}
    for raw, ysym in zip(terms.underlyings, ysyms):
        if ysym in sheet.levels.initial:
            norm_initial[ysym] = sheet.levels.initial[ysym]
        elif raw in sheet.levels.initial:
            norm_initial[ysym] = sheet.levels.initial[raw]
    levels = BarrierLevels(
        initial=norm_initial,
        ko=_remap(sheet.levels.ko, terms.underlyings, ysyms),
        strike=_remap(sheet.levels.strike, terms.underlyings, ysyms),
        ki=_remap(sheet.levels.ki, terms.underlyings, ysyms),
    )

    raw_ratios = detect_split_ratios(levels.initial, closes, sch.trade_date)
    ratios = {s: nearest_clean_ratio(r) for s, r in raw_ratios.items()}

    # 只有比例確實落在乾淨的分割倍數上才換算；否則那是價格對不上，
    # 該讓期初價比對把它顯示出來，而不是悄悄縮放掉。
    clean = {
        s: abs(raw_ratios[s] - r) / r <= 0.005 for s, r in ratios.items()
    }
    ratios = {s: (r if clean[s] else 1.0) for s, r in ratios.items()}
    off = {s: r for s, r in ratios.items() if abs(r - 1.0) > split_tolerance}

    if off and auto_adjust_splits:
        closes = _rescale_prices(closes, ratios)
        desc = "、".join(f"{s} x{r:g}" for s, r in off.items())
        notes.append(
            f"偵測到分割/合併倍數（{desc}），已把行情序列還原成 Term Sheet 當時的"
            "價格尺度。Yahoo 收盤價已還原分割，而條款確認書記載的是發行當時的原始報價；"
            "整股交割須在原始尺度上計算，否則零股找補與總價值會算錯。"
        )
    elif off:
        desc = "、".join(f"{s} x{r:g}" for s, r in off.items())
        notes.append(f"⚠ 文件價位與行情序列相差 {desc}，未自動換算，以下比對可能失真。")

    outcome = evaluate(terms, closes, sch, levels=levels)

    checks: list[Check] = []

    # --- 分割倍數是否為乾淨的比例 ---
    for s, raw in raw_ratios.items():
        clean = ratios[s]
        drift = abs(raw - clean) / clean
        checks.append(Check(
            f"價格尺度 {s}", f"x{clean:g}", f"x{raw:.6g}",
            drift <= 0.005,
            "文件期初價 ÷ 行情收盤價；應為 1 或乾淨的分割倍數",
        ))

    # --- 日程 ---
    checks.append(Check(
        "交易日", str(sheet.trade_date.date()), str(sch.trade_date.date()),
        sheet.trade_date.date() == sch.trade_date.date(),
        "文件指定日若非交易日會順延" if sheet.trade_date != sch.trade_date else "",
    ))
    if sheet.issue_date:
        checks.append(Check(
            "發行日", sheet.issue_date, str(sch.issue_date.date()),
            pd.Timestamp(sheet.issue_date).date() == sch.issue_date.date(),
            f"模型以交易日 + {terms.issue_delay_bd}BD 推算",
        ))
    if sheet.final_valuation:
        checks.append(Check(
            "期末評價日", sheet.final_valuation, str(sch.final_valuation.date()),
            pd.Timestamp(sheet.final_valuation).date() == sch.final_valuation.date(),
        ))

    # --- 價位（已換算到同一尺度後才比對）---
    for s in ysyms:
        market_init = float(closes[s].loc[sch.trade_date])
        stated = float(levels.initial[s])
        checks.append(Check(
            f"期初價 {s}", f"{stated:,.4f}", f"{market_init:,.4f}",
            _approx(stated, market_init, price_tol),
            "文件載明 vs 交易日實際收盤",
        ))

    # --- 結果 ---
    if sheet.expected_scenario:
        checks.append(Check(
            "情境", sheet.expected_scenario, outcome.scenario.value,
            sheet.expected_scenario.strip() == outcome.scenario.value,
        ))
    if sheet.expected_exit_date:
        checks.append(Check(
            "出場/到期日", sheet.expected_exit_date, str(outcome.exit_date.date()),
            pd.Timestamp(sheet.expected_exit_date).date() == outcome.exit_date.date(),
        ))
    if sheet.expected_coupon_total is not None:
        checks.append(Check(
            "配息合計", f"{sheet.expected_coupon_total:,.2f}", f"{outcome.coupon_total:,.2f}",
            _approx(sheet.expected_coupon_total, outcome.coupon_total, money_tol),
        ))
    if sheet.expected_delivered_symbol is not None:
        actual = outcome.delivered_symbol or "（未承接）"
        checks.append(Check(
            "承接標的", sheet.expected_delivered_symbol, actual,
            sheet.expected_delivered_symbol == outcome.delivered_symbol,
        ))
    if sheet.expected_delivery_price is not None:
        checks.append(Check(
            "承接價", f"{sheet.expected_delivery_price:,.4f}", f"{outcome.delivery_price:,.4f}",
            _approx(sheet.expected_delivery_price, outcome.delivery_price, price_tol),
        ))
    if sheet.expected_shares is not None:
        checks.append(Check(
            "承接股數", f"{sheet.expected_shares:,.4f}", f"{outcome.shares:,.4f}",
            _approx(sheet.expected_shares, outcome.shares, 1e-6, rel=True),
        ))
    if sheet.expected_total_value is not None:
        checks.append(Check(
            "總價值", f"{sheet.expected_total_value:,.2f}", f"{outcome.total_value:,.2f}",
            _approx(sheet.expected_total_value, outcome.total_value, money_tol),
        ))

    return Reconciliation(sheet, outcome, sch, checks, ratios, notes)


def _remap(
    d: dict[str, float] | None, raws: list[str], ysyms: list[str]
) -> dict[str, float] | None:
    """允許文件以 Bloomberg 或 Yahoo 代碼標記價位。"""
    if d is None:
        return None
    out: dict[str, float] = {}
    for raw, ysym in zip(raws, ysyms):
        if ysym in d:
            out[ysym] = d[ysym]
        elif raw in d:
            out[ysym] = d[raw]
    return out


def format_reconciliation(rec: Reconciliation) -> str:
    from .report import format_outcome, format_terms

    lines = [format_terms(rec.sheet.terms, rec.schedule), ""]
    lines.append("── 對帳結果 " + "─" * 64)
    if rec.notes:
        for n in rec.notes:
            lines.append(f"  註：{n}")
        lines.append("")

    lines.append(f"  {'項目':<18}{'Term Sheet':>20}{'模型':>20}   結果")
    lines.append("  " + "─" * 72)
    for c in rec.checks:
        lines.append(f"  {c.item:<18}{c.expected:>20}{c.actual:>20}   {c.mark}"
                     + (f"   （{c.note}）" if c.note else ""))

    n_cmp = len(rec.compared)
    n_fail = len(rec.failures)
    lines.append("")
    lines.append(
        f"  比對 {n_cmp} 項，{n_cmp - n_fail} 項相符"
        + ("，全部通過 ✓" if n_fail == 0 else f"，{n_fail} 項不符 ✗")
    )
    lines.append("")
    lines.append(format_outcome(rec.outcome))
    return "\n".join(lines)
