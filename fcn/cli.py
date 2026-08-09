"""FCN 報價/模擬/回測 命令列介面。

範例（永豐金證券條件表）::

    python -m fcn.cli quote --ud "TSM UN" --ud "NVDA UW" --ud "MSFT UW" \\
        --tenor 12 --strike 80 --autocall 100 --ki 60 --ki-type AKI

    python -m fcn.cli backtest --ud "TSM UN" --ud "NVDA UW" --ud "MSFT UW" \\
        --coupon 12 --years 5

    python -m fcn.cli path --ud "NVDA UW" --coupon 12 --trade-date 2024-06-03
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from . import mc, report
from .backtest import run_backtest
from .data import fetch_risk_free_rate, load_market_data
from .engine import evaluate
from .schedule import build_schedule
from .terms import AutocallCoupon, CouponFreq, FCNTerms, KIType, KOType

_KO_CHOICES = {k.value.lower(): k for k in KOType}
_KI_CHOICES = {k.value.lower(): k for k in KIType} | {"na": KIType.NONE, "none": KIType.NONE}
_FREQ_CHOICES = {k.value.lower(): k for k in CouponFreq}

# --solve 的簡稱 -> FCNTerms 欄位名
_SOLVE_FIELDS = {
    "coupon": "coupon_pa",
    "rebate": "rebate",
    "strike": "strike_pct",
    "ki": "ki_pct",
    "autocall": "autocall_pct",
    "lower-call": "lower_call_strike_pct",
}


def _add_terms_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("報價條件設定")
    g.add_argument("--ud", action="append", required=True, metavar="TICKER",
                   help="連結標的，可重複 1~4 次（Bloomberg 或 Yahoo 代碼）")
    g.add_argument("--ccy", default="USD", help="計價幣別（預設 USD）")
    g.add_argument("--tenor", type=int, default=12, help="天期（月），預設 12")
    g.add_argument("--coupon", type=float, default=None,
                   help="年化配息 %%；省略則由模型反推公允值")
    g.add_argument("--i-delay", type=int, default=5, help="發行日遞延營業日數，預設 5")
    g.add_argument("--freq", default="Monthly", help="配息頻率 Monthly/Quarterly/Semiannual/Annual")
    g.add_argument("--strike", type=float, default=80.0, help="執行價 %%，預設 80")
    g.add_argument("--ko-type", default="Daily Memory",
                   help="提前出場型式：Daily Memory / Daily / Monthly Memory / Monthly / None")
    g.add_argument("--autocall", type=float, default=100.0, help="提前出場價 %%，預設 100")
    g.add_argument("--ki-type", default="AKI", help="觸及生效型式：AKI / EKI / NA")
    g.add_argument("--ki", type=float, default=60.0, help="下限價 %%，預設 60")
    g.add_argument("--lower-call", type=float, default=None, metavar="PCT",
                   help="參與表現價 %%（Upside FCN 專屬，如 103）；省略則為一般 FCN")
    g.add_argument("--participation", type=float, default=100.0,
                   help="參與率 %%，預設 100")
    g.add_argument("--rebate", type=float, default=0.0,
                   help="行銷通路費 %%（平台範圍 0.2~3）")
    g.add_argument("--notional", type=float, default=100_000.0, help="面額，預設 100,000")
    g.add_argument("--lockout", type=int, default=1, help="KO 鎖定期（月），預設 1")
    g.add_argument("--tenor-from", default="trade", choices=["trade", "issue"],
                   help="天期起算基準，預設交易日")
    g.add_argument("--fractional-shares", action="store_true",
                   help="承接時允許零股（預設只交割整股 + 現金找補）")
    g.add_argument("--full-period-coupon", action="store_true",
                   help="提前出場配息採「完整期數」而非 ACT/365 累計計息")


def _build_terms(a: argparse.Namespace) -> FCNTerms:
    ki_type = _KI_CHOICES.get(str(a.ki_type).lower())
    if ki_type is None:
        raise SystemExit(f"未知的 KI Type：{a.ki_type}")
    ko_type = _KO_CHOICES.get(str(a.ko_type).lower())
    if ko_type is None:
        raise SystemExit(f"未知的 KO Type：{a.ko_type}")
    freq = _FREQ_CHOICES.get(str(a.freq).lower())
    if freq is None:
        raise SystemExit(f"未知的配息頻率：{a.freq}")

    return FCNTerms(
        underlyings=a.ud,
        currency=a.ccy,
        tenor_months=a.tenor,
        coupon_pa=None if a.coupon is None else a.coupon / 100.0,
        issue_delay_bd=a.i_delay,
        coupon_freq=freq,
        strike_pct=a.strike / 100.0,
        ko_type=ko_type,
        autocall_pct=a.autocall / 100.0,
        ki_type=ki_type,
        ki_pct=None if ki_type is KIType.NONE else a.ki / 100.0,
        lower_call_strike_pct=None if a.lower_call is None else a.lower_call / 100.0,
        participation=a.participation / 100.0,
        rebate=a.rebate / 100.0,
        notional=a.notional,
        ko_lockout_months=a.lockout,
        tenor_from=a.tenor_from,
        integer_shares=not a.fractional_shares,
        autocall_coupon=(
            AutocallCoupon.FULL_PERIODS if a.full_period_coupon else AutocallCoupon.ACCRUED
        ),
    )


def _load(terms: FCNTerms, years: float, end: str | None = None):
    end_ts = pd.Timestamp(end) if end else pd.Timestamp.today().normalize()
    start_ts = end_ts - pd.DateOffset(years=int(years) + 1)
    return load_market_data(terms.underlyings, start_ts, end_ts)


def cmd_quote(a: argparse.Namespace) -> int:
    terms = _build_terms(a)
    md = _load(terms, a.lookback_years, a.asof)
    ysyms = [md.symbol_map[u] for u in terms.underlyings]

    rate = a.rate / 100.0 if a.rate is not None else fetch_risk_free_rate()
    mp = mc.calibrate(
        md.adjcloses[ysyms],
        rate=rate,
        div_yields=md.dividend_yields(),
        lookback_days=int(a.lookback_years * 252),
    )
    mp.spot = md.closes[ysyms].iloc[-1].to_numpy(dtype=float)   # 障礙以未還原股息價為準

    trade_date = pd.Timestamp(a.asof) if a.asof else md.trading_days[-1]

    # 詢價平台慣例：欲詢價的欄位留白。--solve 指定要解哪一欄；
    # 未指定但 --coupon 留白時，預設求解年化配息。
    field = _SOLVE_FIELDS.get(a.solve) if a.solve else ("coupon_pa" if a.coupon is None else None)

    solved = None
    used = terms
    if field is not None:
        try:
            solved = mc.solve(
                terms, mp, field=field, trade_date=trade_date, n_paths=a.solve_paths
            )
        except ValueError as exc:
            raise SystemExit(f"詢價失敗：{exc}") from None
        used = solved.terms

    pricing = mc.price(used, mp, trade_date=trade_date, n_paths=a.paths)
    fc = mc.forecast(used, mp, trade_date=trade_date, n_paths=a.paths)

    print(report.format_full_report(used, pricing.schedule, pricing=pricing, forecast=fc,
                                    solve=solved))
    return 0


def cmd_backtest(a: argparse.Namespace) -> int:
    terms = _build_terms(a)
    md = _load(terms, a.years, a.asof)
    ysyms = [md.symbol_map[u] for u in terms.underlyings]

    if terms.coupon_pa is None:
        rate = a.rate / 100.0 if a.rate is not None else fetch_risk_free_rate()
        mp = mc.calibrate(md.adjcloses[ysyms], rate=rate, div_yields=md.dividend_yields())
        mp.spot = md.closes[ysyms].iloc[-1].to_numpy(dtype=float)
        fair = mc.price(terms, mp, trade_date=md.trading_days[-1], n_paths=a.paths).fair_coupon_pa
        terms = terms.with_coupon(fair)
        print(f"[未指定 --coupon，採用模型公允配息率 {fair:.2%}]\n")

    bt = run_backtest(terms, md, step_days=a.step)
    print(report.format_full_report(terms, backtest=bt))
    if a.csv:
        report.outcomes_to_csv(bt, a.csv)
        print(f"\n[明細已輸出：{a.csv}]")
    return 0


def cmd_path(a: argparse.Namespace) -> int:
    terms = _build_terms(a)
    if terms.coupon_pa is None:
        raise SystemExit("path 子命令需要 --coupon")

    md = _load(terms, a.tenor / 12 + 2, a.end)
    sch = build_schedule(terms, md.trading_days, pd.Timestamp(a.trade_date))
    if sch.final_valuation > md.trading_days[-1]:
        raise SystemExit(
            f"行情資料只到 {md.trading_days[-1].date()}，"
            f"無法涵蓋至期末評價日 {sch.final_valuation.date()}"
        )
    outcome = evaluate(terms, md.closes, sch)
    print(report.format_full_report(terms, sch, outcome=outcome))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="fcn", description="FCN 境外結構型商品 報價 / 模擬 / 回測"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("quote", help="反推公允配息率 + 情境機率 + 損益分布預測")
    _add_terms_args(q)
    q.add_argument("--solve", choices=sorted(_SOLVE_FIELDS), default=None,
                   help="指定要詢價（留白）的欄位；預設為 coupon（當 --coupon 未給時）")
    q.add_argument("--solve-paths", type=int, default=20_000, help="求解時的蒙地卡羅路徑數")
    q.add_argument("--paths", type=int, default=100_000, help="蒙地卡羅路徑數")
    q.add_argument("--lookback-years", type=float, default=2.0, help="波動/相關性校準回顧年數")
    q.add_argument("--rate", type=float, default=None, help="無風險利率 %%（省略則取 ^IRX）")
    q.add_argument("--asof", default=None, help="以哪一天為交易日（預設最新交易日）")
    q.set_defaults(func=cmd_quote)

    b = sub.add_parser("backtest", help="歷史回測：每隔 N 個交易日進場一次")
    _add_terms_args(b)
    b.add_argument("--years", type=float, default=5.0, help="回測涵蓋年數")
    b.add_argument("--step", type=int, default=5, help="進場間隔（交易日），預設 5")
    b.add_argument("--paths", type=int, default=40_000, help="反推配息時的路徑數")
    b.add_argument("--rate", type=float, default=None, help="無風險利率 %%")
    b.add_argument("--asof", default=None, help="回測截止日")
    b.add_argument("--csv", default=None, help="輸出逐筆明細 CSV")
    b.set_defaults(func=cmd_backtest)

    s = sub.add_parser("path", help="單一歷史進場日的逐項判定明細")
    _add_terms_args(s)
    s.add_argument("--trade-date", required=True, help="交易日 YYYY-MM-DD")
    s.add_argument("--end", default=None, help="行情擷取截止日")
    s.set_defaults(func=cmd_path)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
