"""文字報表輸出。"""

from __future__ import annotations

import pandas as pd

from .backtest import BacktestResult
from .engine import FCNOutcome, Scenario
from .mc import ForecastResult, PricingResult
from .schedule import Schedule
from .terms import FCNTerms, KIType


def _pct(x: float | None, nd: int = 2) -> str:
    return "—" if x is None else f"{x * 100:.{nd}f}%"


def _money(x: float, ccy: str = "") -> str:
    return f"{x:,.0f}" + (f" {ccy}" if ccy else "")


def _rule(title: str = "", width: int = 78) -> str:
    if not title:
        return "─" * width
    pad = width - len(title) * 2 - 2
    return f"── {title} " + "─" * max(pad, 3)


def format_terms(terms: FCNTerms, schedule: Schedule | None = None) -> str:
    ki = "NA" if terms.ki_type is KIType.NONE else f"{terms.ki_pct:.0%}"
    ki_type = "NA" if terms.ki_type is KIType.NONE else terms.ki_type.value
    cpn = "（待報價）" if terms.coupon_pa is None else f"{terms.coupon_pa:.2%}"

    lines = [
        _rule("報價條件"),
        f"  產品 (Product)      {terms.product_name}",
        f"  連結標的 (UD1~UD4)  {' / '.join(terms.underlyings)}",
        f"  幣別 / 面額         {terms.currency} / {_money(terms.notional)}",
        f"  天期 (Tenor)        {terms.tenor_months} 個月",
        f"  年化配息 (Cpn p.a.) {cpn}",
        f"  配息頻率            {terms.coupon_freq.value}（共 {terms.n_coupons} 期）",
        f"  發行日 (I Delay)    交易日 + {terms.issue_delay_bd}BD",
        f"  執行價 (Put Strike) {terms.strike_pct:.2%}",
        f"  提前出場 (Autocall) {terms.autocall_pct:.2%}   型式 {terms.ko_type.value}"
        f"（鎖定期 {terms.ko_lockout_months} 個月）",
        f"  下限價 (KI Level)   {ki}   型式 {ki_type}",
        f"  行銷通路費 (Rebate) {terms.rebate:.2%}",
    ]
    breaches = terms.check_platform_limits()
    if breaches:
        lines.append("  ⚠ 超出詢價平台可受理範圍：")
        lines += [f"      - {b}" for b in breaches]
    if terms.is_upside:
        lines.append(
            f"  參與表現價 (Lower Call Strike) {terms.lower_call_strike_pct:.2%}"
            f"   參與率 {terms.participation:.0%}"
        )
        lines.append(
            f"    還本金額 = 面額 x [100% + {terms.participation:.0%} x "
            f"max(0%, 最差標的表現 - {terms.lower_call_strike_pct:.0%})]"
        )
    if schedule is not None:
        lines += [
            "",
            f"  交易日 / 期初定價   {schedule.trade_date.date()}",
            f"  發行日              {schedule.issue_date.date()}",
            f"  期末評價日          {schedule.final_valuation.date()}",
            f"  KO 觀察期間         {schedule.ko_days[0].date()} ~ "
            f"{schedule.ko_days[-1].date()}（{len(schedule.ko_days)} 個觀察日，不含期末評價日）"
            if len(schedule.ko_days)
            else "  KO 觀察期間         無",
            f"  KI 觀察期間         "
            + (
                f"{schedule.ki_days[0].date()} ~ {schedule.ki_days[-1].date()}"
                f"（{len(schedule.ki_days)} 個觀察日）"
                if len(schedule.ki_days)
                else "無"
            ),
        ]
    return "\n".join(lines)


def format_levels(outcome: FCNOutcome) -> str:
    t = outcome.terms
    extra = f"{'參與表現價':>14}" if t.is_upside else ""
    rows = [f"  {'標的':<8}{'期初價':>12}{'提前出場價':>14}{extra}{'執行價':>12}{'下限價':>12}"]
    for s in outcome.initial_prices.index:
        ki = "—" if outcome.ki_levels is None else f"{outcome.ki_levels[s]:>12,.2f}"
        lc = (
            f"{outcome.initial_prices[s] * t.lower_call_strike_pct:>14,.2f}"
            if t.is_upside
            else ""
        )
        rows.append(
            f"  {s:<8}{outcome.initial_prices[s]:>12,.2f}"
            f"{outcome.ko_levels[s]:>14,.2f}{lc}"
            f"{outcome.strike_levels[s]:>12,.2f}{ki:>12}"
        )
    rows.append(
        f"  （提前出場價 = 期初 x {t.autocall_pct:.0%}；執行價 = 期初 x {t.strike_pct:.0%}"
        + ("）" if outcome.ki_levels is None else f"；下限價 = 期初 x {t.ki_pct:.0%}）")
    )
    return "\n".join(rows)


def format_outcome(outcome: FCNOutcome) -> str:
    t = outcome.terms
    ccy = t.currency
    lines = [
        _rule("給付結果"),
        f"  情境        {outcome.scenario.value}",
        f"  出場日      {outcome.exit_date.date()}（自發行日持有 {outcome.days_held} 天）",
        "",
        "  記憶事件 (>= 提前出場價)",
    ]
    for s, d in outcome.memory_dates.items():
        lines.append(f"    {s:<8}{d.date() if d is not None else '未發生'}")
    lines.append("  觸及生效事件 (< 下限價)")
    for s, d in outcome.ki_dates.items():
        lines.append(f"    {s:<8}{d.date() if d is not None else '未發生'}")

    lines += [
        "",
        "  期末表現",
    ]
    for s in outcome.performances.index:
        mark = "  <= 最差" if s == outcome.worst_symbol else ""
        lines.append(
            f"    {s:<8}{outcome.final_prices[s]:>10,.2f}"
            f"  ({outcome.performances[s] - 1:+.2%}){mark}"
        )

    lines += ["", "  現金流"]
    lines.append(f"    配息合計          {_money(outcome.coupon_total, ccy):>18}")
    if outcome.scenario is Scenario.DELIVERY:
        lines += [
            f"    承接標的          {outcome.delivered_symbol:>18}",
            f"    承接價（執行價）  {outcome.delivery_price:>18,.2f}",
            f"    承接股數          {outcome.shares:>18,.0f}",
            f"    股票市值（期末）  {_money(outcome.stock_value, ccy):>18}",
            f"    現金找補          {_money(outcome.residual_cash, ccy):>18}",
        ]
    else:
        lines.append(f"    本金返還          {_money(outcome.principal_returned, ccy):>18}")
        if t.is_upside:
            excess = max(0.0, outcome.worst_performance - t.lower_call_strike_pct)
            lines.append(
                f"    參與漲幅          {_money(outcome.upside_payment, ccy):>18}"
                f"   (最差表現 {outcome.worst_performance:.2%} - 參與表現價 "
                f"{t.lower_call_strike_pct:.0%} = {excess:.2%})"
            )

    lines += [
        "",
        f"    總價值            {_money(outcome.total_value, ccy):>18}",
        f"    損益              {_money(outcome.pnl, ccy):>18}"
        f"   ({outcome.return_pct:+.2%}，年化 {outcome.annualized_pct:+.2%})",
        "",
        f"  對照：同期直接買進最差標的 {outcome.worst_symbol} "
        f"{outcome.buy_and_hold_return():+.2%}"
        f"  →  FCN {'勝出' if outcome.return_pct > outcome.buy_and_hold_return() else '落後'} "
        f"{abs(outcome.return_pct - outcome.buy_and_hold_return()) * 100:.2f} 個百分點",
    ]

    if outcome.warnings:
        lines += ["", "  ⚠ 條款提醒"]
        lines += [f"    - {w}" for w in outcome.warnings]
    return "\n".join(lines)


def format_pricing(pr: PricingResult, terms: FCNTerms) -> str:
    mp = pr.market
    lines = [
        _rule("市場參數校準"),
        f"  無風險利率  {mp.rate:.2%}",
        f"  {'標的':<8}{'期初價':>12}{'年化波動':>12}{'股息殖利率':>12}",
    ]
    for i, s in enumerate(mp.symbols):
        lines.append(
            f"  {s:<8}{mp.spot[i]:>12,.2f}{mp.vol[i]:>12.2%}{mp.div_yield[i]:>12.2%}"
        )
    lines.append("  相關係數矩陣")
    lines.append("          " + "".join(f"{s:>9}" for s in mp.symbols))
    for i, s in enumerate(mp.symbols):
        lines.append(f"  {s:<8}" + "".join(f"{mp.corr[i][j]:>9.2f}" for j in range(len(mp.symbols))))

    lines += [
        "",
        _rule("風險中性定價"),
        f"  蒙地卡羅路徑數      {pr.n_paths:,}",
        f"  ★ 公允年化配息率    {pr.fair_coupon_pa:.2%}"
        + (f"（已扣 {pr.rebate:.2%} 通路費）" if pr.rebate else ""),
    ]
    if pr.rebate:
        lines.append(f"    未扣通路費者        {pr.fair_coupon_gross:.2%}")
    if pr.quoted_coupon_pa is not None:
        gap = pr.value_gap_pct or 0.0
        verdict = "偏低（投資人吃虧）" if gap < -0.005 else ("合理" if gap < 0.005 else "偏高（划算）")
        lines += [
            f"  券商報價配息率      {pr.quoted_coupon_pa:.2%}",
            f"    配息缺口          {(pr.quoted_coupon_pa - pr.fair_coupon_pa) * 100:+.2f} 個百分點",
            f"  以報價計算的理論價值 {pr.pv_at_quote * 100:.2f}%（面額 100%）",
            f"  價值差距            {gap * 100:+.2f} 個百分點  →  {verdict}",
            f"    其中 報價單通路費  {pr.rebate * 100:.2f} 個百分點",
            f"    其中 發行商保留    {(pr.issuer_margin or 0.0) * 100:.2f} 個百分點",
        ]
    lines += [
        "",
        f"  預期存續期間        {pr.expected_life_years:.2f} 年"
        f"（契約 {terms.tenor_months / 12:.2f} 年）",
        f"  觸及生效 (KI) 機率  "
        + ("—（無 KI 條件）" if terms.ki_type is KIType.NONE else f"{pr.prob_ki:.2%}"),
        f"  ★ 承接股票機率      {pr.prob_delivery:.2%}",
        "",
        "  情境機率（風險中性測度）",
    ]
    for k, v in pr.scenario_probs.items():
        lines.append(f"    {k:<26}{v:>8.2%}")
    return "\n".join(lines)


_SOLVE_LABELS = {
    "coupon_pa": "年化配息 (Cpn p.a.)",
    "rebate": "行銷通路費 (Rebate)",
    "strike_pct": "執行價 (Put Strike)",
    "ki_pct": "下限價 (KI Level)",
    "autocall_pct": "提前出場價 (Autocall)",
    "lower_call_strike_pct": "參與表現價 (Lower Call Strike)",
}


def format_solve(sr) -> str:
    """格式化「留白欄位」的求解結果。"""
    label = _SOLVE_LABELS.get(sr.field, sr.field)
    lines = [
        _rule("詢價結果（留白欄位求解）"),
        f"  留白欄位            {label}",
        f"  ★ 求得數值          {sr.value:.4%}",
        f"  對應理論價值        {sr.pv * 100:.4f}%（目標 {(1 - sr.terms.rebate) * 100:.2f}%）",
        f"  蒙地卡羅路徑數      {sr.n_paths:,}"
        + (f"（二分搜尋 {sr.iterations} 次）" if sr.iterations else "（封閉解）"),
    ]
    breaches = sr.terms.check_platform_limits()
    if breaches:
        lines.append("  ⚠ 解出的條款超出詢價平台可受理範圍：")
        lines += [f"      - {b}" for b in breaches]
    return "\n".join(lines)


def format_forecast(fc: ForecastResult) -> str:
    lines = [_rule("損益分布預測（真實機率測度）"), "", "  情境機率"]
    for k, v in fc.scenario_probs.items():
        lines.append(f"    {k:<26}{v:>8.2%}")
    lines += ["", "  報酬統計"]
    for k, v in fc.summary().items():
        lines.append(f"    {k:<30}{_pct(v):>10}")

    lines += ["", "  報酬分位數"]
    import numpy as np

    for q in (1, 5, 10, 25, 50, 75, 90, 99):
        lines.append(f"    P{q:<3}{_pct(float(np.percentile(fc.returns, q))):>34}")
    return "\n".join(lines)


def format_backtest(bt: BacktestResult) -> str:
    s = bt.summary()
    if s.get("樣本數", 0) == 0:
        return _rule("歷史回測") + "\n  資料期間不足以完成任何一個完整契約期間。"

    lines = [_rule("歷史回測"), ""]
    for k, v in s.items():
        if k in ("樣本數",):
            lines.append(f"  {k:<30}{v:>10,}")
        elif k == "平均持有天數":
            lines.append(f"  {k:<30}{v:>10.0f}")
        else:
            lines.append(f"  {k:<30}{_pct(float(v)):>10}")

    lines += ["", "  各情境明細"]
    bd = bt.scenario_breakdown()
    lines.append(f"    {'情境':<28}{'次數':>6}{'占比':>9}{'平均報酬':>11}{'最差':>11}{'最佳':>11}")
    for scen, row in bd.iterrows():
        lines.append(
            f"    {scen:<28}{int(row['次數']):>6}{row['占比']:>9.1%}"
            f"{row['平均報酬']:>11.2%}{row['最差']:>11.2%}{row['最佳']:>11.2%}"
        )

    df = bt.to_frame()
    worst = df.nsmallest(5, "報酬率")
    lines += ["", "  最差 5 次進場"]
    lines.append(f"    {'交易日':<12}{'情境':<28}{'報酬率':>10}{'承接標的':>10}")
    for _, r in worst.iterrows():
        lines.append(
            f"    {str(r['交易日']):<12}{r['情境']:<28}{r['報酬率']:>10.2%}{r['承接標的']:>10}"
        )
    return "\n".join(lines)


def format_full_report(
    terms: FCNTerms,
    schedule: Schedule | None = None,
    pricing: PricingResult | None = None,
    forecast: ForecastResult | None = None,
    backtest: BacktestResult | None = None,
    outcome: FCNOutcome | None = None,
    solve=None,
) -> str:
    parts = [format_terms(terms, schedule)]
    if solve is not None:
        parts.append(format_solve(solve))
    if outcome is not None:
        parts += [format_levels(outcome), format_outcome(outcome)]
    if pricing is not None:
        parts.append(format_pricing(pricing, terms))
    if forecast is not None:
        parts.append(format_forecast(forecast))
    if backtest is not None:
        parts.append(format_backtest(backtest))
    parts.append(
        _rule()
        + "\n本工具為條款模擬與教育用途，非投資建議；實際給付以發行機構之產品說明書與條款確認書為準。"
    )
    return "\n\n".join(parts)


def outcomes_to_csv(bt: BacktestResult, path: str) -> None:
    bt.to_frame().to_csv(path, index=False, encoding="utf-8-sig")
