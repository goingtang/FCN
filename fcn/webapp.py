"""FCN 詢價平台網頁介面。

以標準函式庫的 :mod:`http.server` 實作，除 numpy / pandas 外無額外相依。

啟動::

    python -m fcn.webapp            # http://127.0.0.1:8000
    python -m fcn.webapp --port 9000 --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import hmac
import http.cookies
import json
import mimetypes
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pandas as pd

from . import mc
from .backtest import run_backtest
from .data import fetch_bars, fetch_risk_free_rate, load_market_data
from .engine import Scenario, evaluate
from .schedule import build_schedule
from .terms import AutocallCoupon, CouponFreq, FCNTerms, KIType, KOType

WEB_DIR = Path(__file__).resolve().parent / "web"

_KO_BY_VALUE = {k.value: k for k in KOType}
_KI_BY_VALUE = {k.value: k for k in KIType} | {"NA": KIType.NONE}
_FREQ_BY_VALUE = {k.value: k for k in CouponFreq}

_SOLVE_LABELS = {
    "coupon_pa": "年化配息 (Cpn p.a.)",
    "rebate": "行銷通路費 (Rebate)",
    "strike_pct": "執行價 (Put Strike)",
    "ki_pct": "下限價 (KI Level)",
    "autocall_pct": "提前出場價 (Autocall)",
    "lower_call_strike_pct": "參與表現價 (Lower Call Strike)",
}


class ApiError(Exception):
    """回傳給前端的可讀錯誤。"""


# --------------------------------------------------------------------------
# 行情快取
# --------------------------------------------------------------------------

_md_cache: dict[tuple, object] = {}
_md_lock = threading.Lock()


def _market_range(tickers: list[str], start, end):
    """抓取指定區間的行情（含快取）。"""
    start_ts, end_ts = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    key = (tuple(tickers), str(start_ts.date()), str(end_ts.date()))
    with _md_lock:
        if key in _md_cache:
            return _md_cache[key]
    md = load_market_data(tickers, start_ts, end_ts)
    with _md_lock:
        _md_cache[key] = md
    return md


def _market_data(tickers: list[str], years: float, end: str | None):
    """自 ``end``（預設今日）往回抓 ``years`` 年（多留一年緩衝）。"""
    end_ts = pd.Timestamp(end) if end else pd.Timestamp.today().normalize()
    return _market_range(tickers, end_ts - pd.DateOffset(years=int(years) + 1), end_ts)


_rate_cache: dict[str, float] = {}


def _risk_free() -> float:
    if "r" not in _rate_cache:
        _rate_cache["r"] = fetch_risk_free_rate()
    return _rate_cache["r"]


# --------------------------------------------------------------------------
# 請求解析
# --------------------------------------------------------------------------


def _pct(payload: dict, key: str) -> float | None:
    """讀取百分比欄位；空字串或 null 代表「留白」。"""
    v = payload.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(v) / 100.0
    except (TypeError, ValueError):
        raise ApiError(f"欄位 {key} 不是有效數字：{v!r}") from None


def terms_from_payload(payload: dict) -> tuple[FCNTerms, str | None]:
    """把前端表單轉成 FCNTerms，並回報哪一個欄位留白（待詢價）。"""
    uds = [str(u).strip() for u in payload.get("underlyings", []) if str(u).strip()]
    if not 1 <= len(uds) <= 4:
        raise ApiError("連結標的須填 1~4 檔（UD1~UD4）")

    ki_type = _KI_BY_VALUE.get(payload.get("ki_type", "AKI"))
    if ki_type is None:
        raise ApiError(f"未知的 KI Type：{payload.get('ki_type')!r}")
    ko_type = _KO_BY_VALUE.get(payload.get("ko_type", "Daily Memory"))
    if ko_type is None:
        raise ApiError(f"未知的 KO Type：{payload.get('ko_type')!r}")
    freq = _FREQ_BY_VALUE.get(payload.get("coupon_freq", "Monthly"))
    if freq is None:
        raise ApiError(f"未知的配息頻率：{payload.get('coupon_freq')!r}")

    upside = bool(payload.get("upside"))
    values = {
        "coupon_pa": _pct(payload, "coupon_pa"),
        "strike_pct": _pct(payload, "strike_pct"),
        "autocall_pct": _pct(payload, "autocall_pct"),
        "ki_pct": _pct(payload, "ki_pct") if ki_type is not KIType.NONE else None,
        "rebate": _pct(payload, "rebate"),
        "lower_call_strike_pct": _pct(payload, "lower_call_strike_pct") if upside else None,
    }

    # 哪些欄位可以留白詢價
    blankable = ["coupon_pa", "strike_pct", "autocall_pct", "rebate"]
    if ki_type is not KIType.NONE:
        blankable.append("ki_pct")
    if upside:
        blankable.append("lower_call_strike_pct")

    blanks = [f for f in blankable if values[f] is None]
    if len(blanks) > 1:
        names = "、".join(_SOLVE_LABELS[f] for f in blanks)
        raise ApiError(f"一次只能留白一個欄位詢價，目前留白：{names}")
    solve_field = blanks[0] if blanks else None

    if ko_type is not KOType.NONE and values["autocall_pct"] is None and solve_field != "autocall_pct":
        raise ApiError("提前出場價未填")

    # 求解時先填入區間中點當佔位值，通過建構驗證
    placeholder = {
        "coupon_pa": 0.10, "strike_pct": 0.80, "autocall_pct": 1.00,
        "ki_pct": 0.60, "rebate": 0.0, "lower_call_strike_pct": 1.05,
    }
    filled = dict(values)
    if solve_field is not None:
        filled[solve_field] = placeholder[solve_field]
        if solve_field == "ki_pct" and filled["strike_pct"] is not None:
            filled["ki_pct"] = max(0.50, min(0.60, filled["strike_pct"] - 0.05))
        if solve_field == "strike_pct" and filled["ki_pct"] is not None:
            filled["strike_pct"] = min(1.0, filled["ki_pct"] + 0.05)
        if solve_field == "lower_call_strike_pct" and filled["strike_pct"] is not None:
            filled["lower_call_strike_pct"] = max(1.05, filled["strike_pct"] + 0.05)

    try:
        terms = FCNTerms(
            underlyings=uds,
            currency=payload.get("currency", "USD"),
            tenor_months=int(payload.get("tenor_months", 12)),
            coupon_pa=filled["coupon_pa"],
            issue_delay_bd=int(payload.get("issue_delay_bd", 5)),
            coupon_freq=freq,
            strike_pct=filled["strike_pct"] if filled["strike_pct"] is not None else 0.80,
            ko_type=ko_type,
            autocall_pct=filled["autocall_pct"] if filled["autocall_pct"] is not None else 1.0,
            ki_type=ki_type,
            ki_pct=filled["ki_pct"],
            lower_call_strike_pct=filled["lower_call_strike_pct"],
            participation=(_pct(payload, "participation") or 1.0) if upside else 1.0,
            rebate=filled["rebate"] or 0.0,
            notional=float(payload.get("notional", 100_000)),
            ko_lockout_months=int(payload.get("ko_lockout_months", 1)),
            integer_shares=bool(payload.get("integer_shares", True)),
            autocall_coupon=(
                AutocallCoupon.FULL_PERIODS
                if payload.get("autocall_coupon") == "full_periods"
                else AutocallCoupon.ACCRUED
            ),
        )
    except ValueError as exc:
        raise ApiError(str(exc)) from None
    return terms, solve_field


def _calibrate(terms: FCNTerms, payload: dict, md):
    ysyms = [md.symbol_map[u] for u in terms.underlyings]
    rate = _pct(payload, "rate")
    lookback = float(payload.get("lookback_years", 2.0))
    mp = mc.calibrate(
        md.adjcloses[ysyms],
        rate=rate if rate is not None else _risk_free(),
        div_yields=md.dividend_yields(),
        lookback_days=int(lookback * 252),
    )
    mp.spot = md.closes[ysyms].iloc[-1].to_numpy(dtype=float)
    return mp, ysyms


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------


def _terms_json(t: FCNTerms) -> dict:
    return {
        "product": t.product_name,
        "underlyings": t.underlyings,
        "currency": t.currency,
        "tenor_months": t.tenor_months,
        "coupon_pa": t.coupon_pa,
        "issue_delay_bd": t.issue_delay_bd,
        "coupon_freq": t.coupon_freq.value,
        "strike_pct": t.strike_pct,
        "ko_type": t.ko_type.value,
        "autocall_pct": t.autocall_pct,
        "ki_type": "NA" if t.ki_type is KIType.NONE else t.ki_type.value,
        "ki_pct": t.ki_pct,
        "lower_call_strike_pct": t.lower_call_strike_pct,
        "participation": t.participation,
        "rebate": t.rebate,
        "notional": t.notional,
        "n_coupons": t.n_coupons,
        "coupon_per_period": t.coupon_per_period,
        "platform_warnings": t.check_platform_limits(),
    }


def _schedule_json(s) -> dict:
    return {
        "trade_date": str(s.trade_date.date()),
        "issue_date": str(s.issue_date.date()),
        "final_valuation": str(s.final_valuation.date()),
        "ko_days": len(s.ko_days),
        "ko_start": str(s.ko_days[0].date()) if len(s.ko_days) else None,
        "ko_end": str(s.ko_days[-1].date()) if len(s.ko_days) else None,
        "ki_days": len(s.ki_days),
        "ki_start": str(s.ki_days[0].date()) if len(s.ki_days) else None,
        "ki_end": str(s.ki_days[-1].date()) if len(s.ki_days) else None,
        "coupon_dates": [str(d.date()) for d in s.coupon_dates],
    }


def _market_json(mp) -> dict:
    return {
        "symbols": list(mp.symbols),
        "spot": [float(x) for x in mp.spot],
        "vol": [float(x) for x in mp.vol],
        "div_yield": [float(x) for x in mp.div_yield],
        "corr": [[float(c) for c in row] for row in mp.corr],
        "rate": float(mp.rate),
    }


def _levels_json(terms: FCNTerms, mp) -> list[dict]:
    out = []
    for i, s in enumerate(mp.symbols):
        spot = float(mp.spot[i])
        out.append(
            {
                "symbol": s,
                "initial": spot,
                "ko": spot * terms.autocall_pct,
                "strike": spot * terms.strike_pct,
                "ki": spot * terms.ki_pct if terms.ki_type is not KIType.NONE else None,
                "lower_call": (
                    spot * terms.lower_call_strike_pct if terms.is_upside else None
                ),
            }
        )
    return out


def _histogram(values: np.ndarray, bins: int = 60) -> dict:
    counts, edges = np.histogram(values, bins=bins)
    return {
        "edges": [float(e) for e in edges],
        "counts": [int(c) for c in counts],
    }


def _payoff_curve(terms: FCNTerms) -> dict:
    """到期損益曲線：以最差標的期末表現為橫軸。"""
    coupon = terms.coupon_pa * terms.tenor_months / 12.0
    xs = np.linspace(0.20, 1.60, 141)
    ki_hit, no_ki, buyhold = [], [], []
    for x in xs:
        upside = (
            terms.participation * max(0.0, x - terms.lower_call_strike_pct)
            if terms.is_upside
            else 0.0
        )
        # 已觸及生效：期末低於執行價即承接
        ki_hit.append((x / terms.strike_pct - 1.0 if x < terms.strike_pct else upside) + coupon)
        # 未觸及生效：無論期末多低都全額還本
        no_ki.append(upside + coupon)
        buyhold.append(x - 1.0)
    return {
        "x": [float(v) for v in xs],
        "ki_hit": [float(v) for v in ki_hit],
        "no_ki": [float(v) for v in no_ki],
        "buy_hold": [float(v) for v in buyhold],
        "coupon_total": float(coupon),
        "strike_pct": terms.strike_pct,
        "ki_pct": terms.ki_pct if terms.ki_type is not KIType.NONE else None,
        "autocall_pct": terms.autocall_pct,
        "lower_call_pct": terms.lower_call_strike_pct,
        "has_ki": terms.ki_type is not KIType.NONE,
    }


# --------------------------------------------------------------------------
# API handlers
# --------------------------------------------------------------------------


def api_quote(payload: dict) -> dict:
    terms, solve_field = terms_from_payload(payload)
    md = _market_data(terms.underlyings, float(payload.get("lookback_years", 2.0)) + 1,
                      payload.get("asof"))
    mp, _ = _calibrate(terms, payload, md)
    trade_date = pd.Timestamp(payload["asof"]) if payload.get("asof") else md.trading_days[-1]

    paths = int(payload.get("paths", 40_000))
    solve_paths = int(payload.get("solve_paths", 20_000))

    solve_json = None
    if solve_field is not None:
        try:
            sr = mc.solve(
                terms, mp, field=solve_field, trade_date=trade_date, n_paths=solve_paths
            )
        except ValueError as exc:
            raise ApiError(f"詢價失敗：{exc}") from None
        terms = sr.terms
        solve_json = {
            "field": sr.field,
            "label": _SOLVE_LABELS.get(sr.field, sr.field),
            "value": float(sr.value),
            "pv": float(sr.pv),
            "target": 1.0 - terms.rebate,
            "iterations": sr.iterations,
            "n_paths": sr.n_paths,
        }

    pricing = mc.price(terms, mp, trade_date=trade_date, n_paths=paths)
    fc = mc.forecast(terms, mp, trade_date=trade_date, n_paths=paths)

    return {
        "terms": _terms_json(terms),
        "solve": solve_json,
        "market": _market_json(mp),
        "levels": _levels_json(terms, mp),
        "schedule": _schedule_json(pricing.schedule),
        "pricing": {
            "fair_coupon_pa": float(pricing.fair_coupon_pa),
            "fair_coupon_gross": float(pricing.fair_coupon_gross),
            "quoted_coupon_pa": (
                None if pricing.quoted_coupon_pa is None else float(pricing.quoted_coupon_pa)
            ),
            "pv_at_quote": None if pricing.pv_at_quote is None else float(pricing.pv_at_quote),
            "value_gap": None if pricing.value_gap_pct is None else float(pricing.value_gap_pct),
            "implied_total_fee": (
                None if pricing.implied_total_fee is None else float(pricing.implied_total_fee)
            ),
            "issuer_margin": (
                None if pricing.issuer_margin is None else float(pricing.issuer_margin)
            ),
            "rebate": float(pricing.rebate),
            "prob_ki": float(pricing.prob_ki),
            "prob_delivery": float(pricing.prob_delivery),
            "expected_life_years": float(pricing.expected_life_years),
            "scenario_probs": pricing.scenario_probs,
            "n_paths": pricing.n_paths,
        },
        "forecast": {
            "scenario_probs": fc.scenario_probs,
            "summary": {k: float(v) for k, v in fc.summary().items()},
            "percentiles": {
                f"P{q}": float(np.percentile(fc.returns, q))
                for q in (1, 5, 10, 25, 50, 75, 90, 99)
            },
            "histogram": _histogram(fc.returns),
            "buy_hold_histogram": _histogram(fc.buy_hold_returns),
        },
        "payoff": _payoff_curve(terms),
    }


def api_backtest(payload: dict) -> dict:
    terms, solve_field = terms_from_payload(payload)
    years = float(payload.get("years", 5.0))
    md = _market_data(terms.underlyings, years + 1, payload.get("asof"))

    if solve_field is not None:
        mp, _ = _calibrate(terms, payload, md)
        try:
            sr = mc.solve(terms, mp, field=solve_field, trade_date=md.trading_days[-1],
                          n_paths=int(payload.get("solve_paths", 20_000)))
        except ValueError as exc:
            raise ApiError(f"詢價失敗：{exc}") from None
        terms = sr.terms

    end_ts = pd.Timestamp(payload["asof"]) if payload.get("asof") else md.trading_days[-1]
    bt = run_backtest(
        terms, md,
        start=end_ts - pd.DateOffset(years=int(years)),
        step_days=int(payload.get("step", 5)),
    )
    if not bt.outcomes:
        raise ApiError("行情期間不足以完成任何一個完整契約期間，請縮短天期或拉長回測年數")

    df = bt.to_frame()
    breakdown = bt.scenario_breakdown().reset_index()
    return {
        "terms": _terms_json(terms),
        "summary": {k: (float(v) if k != "樣本數" else int(v)) for k, v in bt.summary().items()},
        "breakdown": [
            {
                "scenario": str(r["情境"]),
                "count": int(r["次數"]),
                "share": float(r["占比"]),
                "mean": float(r["平均報酬"]),
                "min": float(r["最差"]),
                "max": float(r["最佳"]),
            }
            for _, r in breakdown.iterrows()
        ],
        "rows": [
            {
                "trade_date": str(r["交易日"]),
                "exit_date": str(r["出場日"]),
                "scenario": str(r["情境"]),
                "days": int(r["持有天數"]),
                "return": float(r["報酬率"]),
                "buy_hold": float(r["最差表現"]),
                "worst": str(r["最差標的"]),
                "ki": bool(r["曾觸KI"]),
                "delivered": str(r["承接標的"]),
            }
            for _, r in df.iterrows()
        ],
        "histogram": _histogram(df["報酬率"].to_numpy(), bins=40),
    }


def api_path(payload: dict) -> dict:
    terms, solve_field = terms_from_payload(payload)
    if solve_field is not None:
        raise ApiError(f"路徑檢視需要完整條款，請填入{_SOLVE_LABELS[solve_field]}")

    trade_date = payload.get("trade_date")
    if not trade_date:
        raise ApiError("請指定交易日")
    td = pd.Timestamp(trade_date)

    # 行情區間由交易日決定，而非「自今日往回推」，否則舊的交易日會被悄悄
    # 順延到資料起點，給出看起來合理但完全錯誤的結果。
    today = pd.Timestamp.today().normalize()
    md = _market_range(
        terms.underlyings,
        td - pd.Timedelta(days=10),
        min(td + pd.DateOffset(months=terms.tenor_months) + pd.Timedelta(days=45), today),
    )
    ysyms = [md.symbol_map[u] for u in terms.underlyings]
    try:
        sch = build_schedule(terms, md.trading_days, td)
    except ValueError as exc:
        raise ApiError(str(exc)) from None
    if (sch.trade_date - td).days > 7:
        raise ApiError(
            f"{td.date()} 之後 7 天內沒有共同交易日（最接近的是 {sch.trade_date.date()}），"
            "請確認該日期是否早於標的上市日或落在長假期間"
        )
    if sch.final_valuation > md.trading_days[-1]:
        raise ApiError(
            f"行情只到 {md.trading_days[-1].date()}，無法涵蓋至期末評價日 "
            f"{sch.final_valuation.date()}；此契約尚未到期"
        )

    out = evaluate(terms, md.closes[ysyms], sch)

    window = md.closes[ysyms].loc[sch.trade_date : out.exit_date]
    initial = md.closes[ysyms].loc[sch.trade_date]
    series = {
        s: [round(float(v), 6) for v in (window[s] / initial[s]).to_numpy()] for s in ysyms
    }

    return {
        "terms": _terms_json(terms),
        "schedule": _schedule_json(sch),
        "dates": [str(d.date()) for d in window.index],
        "series": series,
        "levels": {
            "ko": terms.autocall_pct,
            "strike": terms.strike_pct,
            "ki": terms.ki_pct if terms.ki_type is not KIType.NONE else None,
            "lower_call": terms.lower_call_strike_pct,
        },
        "events": {
            "memory": {s: (str(d.date()) if d is not None else None)
                       for s, d in out.memory_dates.items()},
            "ki": {s: (str(d.date()) if d is not None else None)
                   for s, d in out.ki_dates.items()},
        },
        "outcome": {
            "scenario": out.scenario.value,
            "is_delivery": out.scenario is Scenario.DELIVERY,
            "exit_date": str(out.exit_date.date()),
            "days_held": out.days_held,
            "coupon_total": out.coupon_total,
            "principal_returned": out.principal_returned,
            "upside_payment": out.upside_payment,
            "delivered_symbol": out.delivered_symbol,
            "delivery_price": out.delivery_price,
            "shares": out.shares,
            "stock_value": out.stock_value,
            "residual_cash": out.residual_cash,
            "total_value": out.total_value,
            "pnl": out.pnl,
            "return_pct": out.return_pct,
            "annualized_pct": out.annualized_pct,
            "worst_symbol": out.worst_symbol,
            "worst_performance": out.worst_performance,
            "buy_hold_return": out.buy_and_hold_return(),
            "performances": {s: float(v) for s, v in out.performances.items()},
            "final_prices": {s: float(v) for s, v in out.final_prices.items()},
            "initial_prices": {s: float(v) for s, v in out.initial_prices.items()},
            "warnings": out.warnings,
        },
    }


def api_tickers(payload: dict) -> dict:
    """查驗標的代碼：解析成 Yahoo 代碼並回報名稱、幣別、最新價與可用資料區間。"""
    raws = [str(u).strip() for u in payload.get("underlyings", []) if str(u).strip()]
    if not raws:
        raise ApiError("請至少輸入一檔標的")
    if len(raws) > 4:
        raise ApiError("最多 4 檔連結標的")

    end = pd.Timestamp.today().normalize()
    out = []
    for raw in raws:
        try:
            bar = fetch_bars(raw, end - pd.DateOffset(years=8), end)
        except Exception as exc:  # noqa: BLE001 - 逐檔回報，不讓一檔失敗拖垮整批
            out.append({"input": raw, "ok": False, "error": str(exc)})
            continue
        out.append({
            "input": raw,
            "ok": True,
            "symbol": bar.symbol,
            "name": bar.name,
            "currency": bar.currency,
            "exchange": bar.exchange,
            "last": float(bar.close.iloc[-1]),
            "last_date": str(bar.close.index[-1].date()),
            "first_date": str(bar.close.index[0].date()),
            "n_days": int(len(bar.close)),
        })

    ccys = {r["currency"] for r in out if r.get("ok")}
    warnings = []
    if len(ccys) > 1:
        warnings.append(
            f"連結標的橫跨多種幣別（{'、'.join(sorted(ccys))}）；"
            "本模型未處理匯率轉換，請確認商品是否為 Quanto 結構"
        )
    exchanges = {r["exchange"] for r in out if r.get("ok")}
    if len({e.split()[0] for e in exchanges if e}) > 1:
        warnings.append(
            "連結標的分屬不同交易所，觀察日將取各標的交易日的交集，"
            "實際商品的『預定交易日』定義請以條款為準"
        )
    return {"tickers": out, "warnings": warnings}


_ROUTES = {
    "/api/quote": api_quote,
    "/api/backtest": api_backtest,
    "/api/path": api_path,
    "/api/tickers": api_tickers,
}


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 通行碼（部署到公開網址時使用）
# --------------------------------------------------------------------------

COOKIE = "fcn_auth"


def access_token() -> str | None:
    """設定了 ``FCN_TOKEN`` 就啟用通行碼保護；未設定則完全開放。"""
    t = (os.environ.get("FCN_TOKEN") or "").strip()
    return t or None


_LOGIN_PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FCN 詢價平台</title><link rel="stylesheet" href="/static/style.css"></head>
<body><main style="max-width:420px;margin:14vh auto">
<section class="panel"><h2>請輸入通行碼</h2>
<p class="hint">本站已啟用存取保護。通行碼由部署者設定於 <code>FCN_TOKEN</code> 環境變數。</p>
<div class="opts"><div class="opt" style="flex:1">
<label>通行碼</label><input id="t" type="password" style="width:100%" autofocus></div></div>
<div class="actions"><button class="go" id="go">進入</button>
<span class="status" id="msg"></span></div></section></main>
<script>
const go = async () => {
  const r = await fetch('/api/login', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({token: document.getElementById('t').value})});
  if (r.ok) location.href = '/';
  else document.getElementById('msg').textContent = '通行碼不正確';
};
document.getElementById('go').addEventListener('click', go);
document.getElementById('t').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
</script></body></html>"""


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (pd.Timestamp,)):
        return str(o.date())
    raise TypeError(f"無法序列化 {type(o)}")


class Handler(BaseHTTPRequestHandler):
    server_version = "FCNQuote/1.0"

    def log_message(self, fmt, *args):  # noqa: A002 - 沿用父類介面
        print(f"  {self.address_string()} {fmt % args}")

    # ---- helpers ----

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=_json_default).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    # ---- 通行碼 ----

    def _authed(self) -> bool:
        token = access_token()
        if token is None:
            return True
        raw = self.headers.get("Cookie")
        if not raw:
            return False
        try:
            got = http.cookies.SimpleCookie(raw).get(COOKIE)
        except http.cookies.CookieError:
            return False
        return bool(got) and hmac.compare_digest(got.value, token)

    def _send_login(self) -> None:
        self._send(401, _LOGIN_PAGE.encode("utf-8"), "text/html; charset=utf-8")

    def _do_login(self) -> None:
        token = access_token()
        try:
            length = int(self.headers.get("Content-Length") or 0)
            given = str(json.loads(self.rfile.read(length) or b"{}").get("token", ""))
        except Exception:  # noqa: BLE001
            given = ""
        if token is None or not hmac.compare_digest(given, token):
            # 固定延遲，避免以回應時間試探
            self._send_json(401, {"error": "通行碼不正確"})
            return
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Set-Cookie",
            f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800{secure}",
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, rel: str) -> None:
        path = (WEB_DIR / rel).resolve()
        if not str(path).startswith(str(WEB_DIR.resolve())) or not path.is_file():
            self._send_json(404, {"error": "not found"})
            return
        ctype, _ = mimetypes.guess_type(str(path))
        self._send(200, path.read_bytes(), ctype or "application/octet-stream")

    # ---- routes ----

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 介面
        path = self.path.split("?", 1)[0]
        if path == "/api/health":                      # 供 PaaS 健康檢查，不需通行碼
            self._send_json(200, {"ok": True})
        elif path.startswith("/static/"):              # 登入頁也要載得到樣式
            self._send_file(path[len("/static/"):])
        elif not self._authed():
            self._send_login()
        elif path in ("/", "/index.html"):
            self._send_file("index.html")
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/login":
            self._do_login()
            return
        if not self._authed():
            self._send_json(401, {"error": "請先輸入通行碼"})
            return
        handler = _ROUTES.get(path)
        if handler is None:
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:  # noqa: BLE001
            self._send_json(400, {"error": "請求不是有效的 JSON"})
            return
        try:
            self._send_json(200, handler(payload))
        except ApiError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 回傳可讀訊息而非中斷服務
            traceback.print_exc()
            self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})


def _lan_address() -> str:
    """取得本機在區域網路上的位址（純查詢，不會真的送出封包）。"""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def is_public_bind(host: str) -> bool:
    """綁定位址是否會讓本機以外的人連得到。"""
    return host.strip().lower() not in _LOOPBACK


def resolve_bind(host: str | None = None, port: int | None = None) -> tuple[str, int]:
    """決定要綁哪個位址與通訊埠。

    PaaS（Zeabur、Railway、Render 等）以 ``PORT`` 環境變數指派通訊埠，並要求
    服務綁在 ``0.0.0.0`` 才能被路由到；因此偵測到 ``PORT`` 時預設對外綁定。
    明確傳入的參數一律優先。
    """
    env_port = os.environ.get("PORT")
    if port is None:
        port = int(env_port) if env_port else 8000
    if host is None:
        host = os.environ.get("HOST") or ("0.0.0.0" if env_port else "127.0.0.1")
    return host, port


def serve(host: str | None = None, port: int | None = None) -> None:
    host, port = resolve_bind(host, port)
    httpd = ThreadingHTTPServer((host, port), Handler)
    token = access_token()

    if is_public_bind(host):
        try:
            shown = _lan_address()
        except Exception:  # noqa: BLE001 - 取不到就退回使用者輸入的位址
            shown = host
        print(f"FCN 詢價平台已啟動 →  http://{shown}:{port}")
        if token:
            print("🔒 已啟用通行碼保護（FCN_TOKEN）")
        else:
            print(
                "⚠ 已對外開放且未設定通行碼：任何連得到此位址的人都能使用。\n"
                "  部署到公開網址時，請設定環境變數 FCN_TOKEN 啟用存取保護。"
            )
    else:
        print(f"FCN 詢價平台已啟動 →  http://{host}:{port}")
    print("按 Ctrl+C 結束")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fcn.webapp", description="FCN 詢價平台網頁介面")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    a = p.parse_args(argv)
    serve(a.host, a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
