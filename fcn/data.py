"""Yahoo Finance 行情擷取。

直接呼叫 Yahoo v8 chart API（yfinance 內建 session 在多數雲端 IP 會被 429 擋下）。

價格慣例（重要）
----------------
結構型商品的 KO / KI / 執行價比對，用的是**實際成交收盤價**，僅因分割、
合併等公司行為調整，**不因除息調整**。Yahoo chart API 的 ``close`` 正是
「已還原分割、未還原股息」的序列，符合條款慣例；``adjclose``（已還原股息）
會使障礙判定失真，本模組僅保留供計算股息殖利率之用。
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

# Bloomberg 交易所代碼 -> Yahoo 後綴
_BBG_EXCHANGE = {
    "UN": "",       # NYSE
    "UW": "",       # Nasdaq
    "UQ": "",       # Nasdaq
    "UA": "",       # NYSE American
    "US": "",       # 泛指美股
    "TT": ".TW",    # 台灣證交所
    "TW": ".TWO",   # 櫃買
    "HK": ".HK",    # 香港
    "JP": ".T",     # 東京
    "JT": ".T",
    "SP": ".SI",    # 新加坡
    "LN": ".L",     # 倫敦
    "GR": ".DE",    # 德國
    "FP": ".PA",    # 巴黎
    "AU": ".AX",    # 澳洲
    "CN": ".TO",    # 多倫多
}


def to_yahoo_symbol(ticker: str) -> str:
    """把 Bloomberg 代碼（如 ``TSM UN``、``700 HK``）轉成 Yahoo 代碼。

    已經是 Yahoo 格式（無空白）則原樣回傳。
    """
    t = ticker.strip().upper()
    if " " not in t:
        return t
    root, exch = t.rsplit(" ", 1)
    root = root.strip()
    suffix = _BBG_EXCHANGE.get(exch)
    if suffix is None:
        raise ValueError(f"未知的交易所代碼 {exch!r}（來自 {ticker!r}）")
    if suffix == ".HK":                       # 港股須補足 4 碼，700 -> 0700.HK
        root = root.zfill(4)
    return f"{root}{suffix}"


@dataclass
class Bars:
    """單一標的的日線資料。"""

    symbol: str
    close: pd.Series          # 未還原股息（僅還原分割）—— 用於障礙判定
    adjclose: pd.Series       # 已還原股息 —— 僅供殖利率/報酬率估算
    dividends: pd.Series      # 各除息日的現金股息
    currency: str
    exchange: str
    name: str = ""            # 公司/基金全名


def _http_get(url: str, timeout: int = 30, retries: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        for host in _HOSTS:
            target = url.format(host=host)
            req = urllib.request.Request(
                target,
                headers={
                    "User-Agent": _UA,
                    "Accept": "application/json,text/plain,*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read()
            except Exception as exc:  # noqa: BLE001 - 逐一嘗試各主機後再拋出
                last = exc
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Yahoo Finance 請求失敗：{last}")


def fetch_bars(
    symbol: str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    *,
    use_cache: bool = True,
) -> Bars:
    """抓取單一標的在 ``[start, end]`` 的日線資料。"""
    ysym = to_yahoo_symbol(symbol)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    cache = _CACHE_DIR / f"{ysym.replace('.', '_')}_{start_ts:%Y%m%d}_{end_ts:%Y%m%d}.json"
    payload: dict | None = None
    if use_cache and cache.exists():
        try:
            payload = json.loads(cache.read_text())
        except Exception:  # noqa: BLE001 - 快取毀損就重抓
            payload = None

    if payload is None:
        params = urllib.parse.urlencode(
            {
                "period1": int(start_ts.timestamp()),
                "period2": int((end_ts + pd.Timedelta(days=1)).timestamp()),
                "interval": "1d",
                "events": "div,split",
                "includeAdjustedClose": "true",
            }
        )
        quoted = urllib.parse.quote(ysym)
        url = "https://{host}/v8/finance/chart/" + quoted + "?" + params
        payload = json.loads(_http_get(url))
        if use_cache:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(payload))

    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(f"{symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"{symbol}: Yahoo 未回傳資料")
    res = results[0]

    meta = res.get("meta", {})
    tz = meta.get("exchangeTimezoneName", "America/New_York")
    stamps = res.get("timestamp") or []
    if not stamps:
        raise RuntimeError(f"{symbol}: 期間內無交易資料")

    idx = (
        pd.to_datetime(pd.Series(stamps), unit="s", utc=True)
        .dt.tz_convert(tz)
        .dt.normalize()
        .dt.tz_localize(None)
    )
    idx = pd.DatetimeIndex(idx, name="date")

    quote = res["indicators"]["quote"][0]
    close = pd.Series(quote.get("close"), index=idx, dtype="float64", name=ysym)
    adj_block = (res["indicators"].get("adjclose") or [{}])[0]
    adj = pd.Series(
        adj_block.get("adjclose", quote.get("close")), index=idx, dtype="float64", name=ysym
    )

    close = close.dropna()
    adj = adj.reindex(close.index)

    divs_raw = ((res.get("events") or {}).get("dividends") or {}).values()
    if divs_raw:
        d_idx = pd.to_datetime([d["date"] for d in divs_raw], unit="s", utc=True)
        d_idx = pd.DatetimeIndex(d_idx.tz_convert(tz).normalize().tz_localize(None))
        dividends = pd.Series(
            [float(d["amount"]) for d in divs_raw], index=d_idx, name=ysym
        ).sort_index()
    else:
        # 不配息的標的也要保留 DatetimeIndex，否則後續日期比較會 TypeError
        dividends = pd.Series(
            dtype="float64", index=pd.DatetimeIndex([], name="date"), name=ysym
        )

    return Bars(
        symbol=ysym,
        close=close,
        adjclose=adj,
        dividends=dividends,
        currency=meta.get("currency", "USD"),
        exchange=meta.get("fullExchangeName", ""),
        name=meta.get("longName") or meta.get("shortName") or "",
    )


@dataclass
class MarketData:
    """一籃子標的的對齊後行情。"""

    closes: pd.DataFrame            # index=共同交易日, columns=Yahoo 代碼
    adjcloses: pd.DataFrame
    bars: dict[str, Bars]
    symbol_map: dict[str, str]      # 原始輸入代碼 -> Yahoo 代碼

    @property
    def trading_days(self) -> pd.DatetimeIndex:
        return self.closes.index

    def dividend_yields(self, asof: pd.Timestamp | None = None) -> dict[str, float]:
        """以最近 12 個月實配現金股息估算殖利率。"""
        out: dict[str, float] = {}
        for sym, bar in self.bars.items():
            ref = asof if asof is not None else bar.close.index[-1]
            price = bar.close.asof(ref)
            if bar.dividends.empty or not price or price <= 0:
                out[sym] = 0.0
                continue
            idx = pd.DatetimeIndex(bar.dividends.index)
            window = bar.dividends[(idx > ref - pd.DateOffset(years=1)) & (idx <= ref)]
            out[sym] = float(window.sum() / price)
        return out


def load_market_data(
    tickers: list[str],
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    *,
    use_cache: bool = True,
) -> MarketData:
    """抓取多檔標的並對齊到「共同交易日」。

    多標的商品的「預定交易日」取交集：任一標的休市當天不作為觀察日。
    """
    bars: dict[str, Bars] = {}
    symbol_map: dict[str, str] = {}
    for t in tickers:
        bar = fetch_bars(t, start, end, use_cache=use_cache)
        bars[bar.symbol] = bar
        symbol_map[t] = bar.symbol

    closes = pd.DataFrame({s: b.close for s, b in bars.items()}).dropna(how="any")
    adjcloses = pd.DataFrame({s: b.adjclose for s, b in bars.items()}).reindex(closes.index)
    return MarketData(closes=closes, adjcloses=adjcloses, bars=bars, symbol_map=symbol_map)


def fetch_risk_free_rate(default: float = 0.04) -> float:
    """以 ^IRX（13 週美國國庫券）當作無風險利率；失敗時回傳預設值。"""
    try:
        url = "https://{host}/v8/finance/chart/%5EIRX?range=5d&interval=1d"
        data = json.loads(_http_get(url, retries=2))
        meta = data["chart"]["result"][0]["meta"]
        return float(meta["regularMarketPrice"]) / 100.0
    except Exception:  # noqa: BLE001 - 取不到就用預設值
        return default
