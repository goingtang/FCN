"""蒙地卡羅：反推公允配息率（風險中性）與損益分布預測（真實機率）。

向量化實作，與 :mod:`fcn.engine` 的單路徑參考實作邏輯一致，並由
``tests/test_mc.py`` 交叉驗證。

公允配息率的解法
----------------
提前出場與承接與否**完全不受配息率影響**，因此票息腳的現值對配息率
呈線性。無需疊代求根：

    PV = PV_贖回 + c x A     其中 A = 每單位配息率的年金現值
    令 PV = 面額（發行價 100%）  =>  c = (面額 - PV_贖回) / A
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .mktcal import future_trading_days
from .schedule import Schedule, build_schedule
from .terms import AutocallCoupon, FCNTerms, KIType, KOType

_SCENARIO_LABELS = ["提前出場", "到期還本（未觸及生效）", "到期還本（期末>=執行價）", "到期承接股票"]
AUTOCALL, NO_KI, ABOVE_STRIKE, DELIVERY = range(4)


# --------------------------------------------------------------------------
# 市場參數校準
# --------------------------------------------------------------------------


@dataclass
class MarketParams:
    """模擬所需的市場參數。"""

    symbols: list[str]
    spot: np.ndarray            # 期初價
    vol: np.ndarray             # 年化波動度
    div_yield: np.ndarray       # 連續股息殖利率
    corr: np.ndarray            # 相關係數矩陣
    rate: float                 # 無風險利率
    drift: np.ndarray | None = None   # 真實測度下的年化漂移（None = 用 rate - q）

    def chol(self) -> np.ndarray:
        """相關矩陣的 Cholesky 分解（必要時做最近正定修正）。"""
        c = np.asarray(self.corr, dtype=float)
        try:
            return np.linalg.cholesky(c)
        except np.linalg.LinAlgError:
            vals, vecs = np.linalg.eigh(c)
            vals = np.clip(vals, 1e-10, None)
            fixed = vecs @ np.diag(vals) @ vecs.T
            d = np.sqrt(np.diag(fixed))
            fixed = fixed / np.outer(d, d)
            return np.linalg.cholesky(fixed)


def calibrate(
    closes: pd.DataFrame,
    *,
    rate: float,
    div_yields: dict[str, float] | None = None,
    lookback_days: int = 504,
    asof: pd.Timestamp | None = None,
    vol_override: dict[str, float] | None = None,
) -> MarketParams:
    """由歷史（還原股息）收盤價估算波動度、相關性與真實漂移。"""
    hist = closes if asof is None else closes.loc[:asof]
    hist = hist.tail(lookback_days + 1)
    if len(hist) < 60:
        raise ValueError(f"歷史資料不足以校準（僅 {len(hist)} 個交易日）")

    logret = np.log(hist / hist.shift(1)).dropna(how="any")
    ann = 252.0
    vol = logret.std(ddof=1).to_numpy() * np.sqrt(ann)
    mu = logret.mean().to_numpy() * ann + 0.5 * vol**2   # 幾何 -> 算術漂移
    corr = logret.corr().to_numpy()

    syms = list(closes.columns)
    if vol_override:
        vol = np.array([vol_override.get(s, v) for s, v in zip(syms, vol)])
    q = np.array([(div_yields or {}).get(s, 0.0) for s in syms], dtype=float)

    return MarketParams(
        symbols=syms,
        spot=hist.iloc[-1].to_numpy(dtype=float),
        vol=vol,
        div_yield=q,
        corr=corr,
        rate=rate,
        drift=mu,
    )


# --------------------------------------------------------------------------
# 日程 -> 步階遮罩
# --------------------------------------------------------------------------


@dataclass
class StepGrid:
    """把契約日程轉成模擬用的步階索引。"""

    dates: pd.DatetimeIndex     # 交易日 (index 0 = 交易日/期初定價日)
    ko_mask: np.ndarray         # bool, 各步是否為 KO 觀察日
    ki_mask: np.ndarray
    fvd_step: int
    t_years: np.ndarray         # 自發行日起算的年數（可為負，僅發行前幾日）
    coupon_steps: np.ndarray    # 各期配息日對應的步階
    coupon_t: np.ndarray        # 各期配息日的年數
    issue_step: int

    @property
    def n_steps(self) -> int:
        return len(self.dates)


def build_grid(terms: FCNTerms, schedule: Schedule, trading_days: pd.DatetimeIndex) -> StepGrid:
    days = trading_days[
        (trading_days >= schedule.trade_date) & (trading_days <= schedule.final_valuation)
    ]
    ko_mask = days.isin(schedule.ko_days)
    ki_mask = days.isin(schedule.ki_days)
    t_years = np.array([(d - schedule.issue_date).days / 365.0 for d in days])

    steps, ts = [], []
    for cd in schedule.coupon_dates:
        pos = int(days.searchsorted(min(cd, days[-1]), side="left"))
        steps.append(min(pos, len(days) - 1))
        ts.append((cd - schedule.issue_date).days / 365.0)

    return StepGrid(
        dates=days,
        ko_mask=np.asarray(ko_mask),
        ki_mask=np.asarray(ki_mask),
        fvd_step=len(days) - 1,
        t_years=t_years,
        coupon_steps=np.array(steps, dtype=int),
        coupon_t=np.array(ts, dtype=float),
        issue_step=int(days.searchsorted(schedule.issue_date, side="left")),
    )


# --------------------------------------------------------------------------
# 路徑模擬 + 向量化給付判定
# --------------------------------------------------------------------------


def _simulate_chunk(
    mp: MarketParams,
    grid: StepGrid,
    n_paths: int,
    rng: np.random.Generator,
    *,
    risk_neutral: bool,
) -> np.ndarray:
    """產生 (n_paths, n_steps, n_assets) 的 GBM 路徑，index 0 = 期初價。"""
    n_assets = len(mp.spot)
    n_steps = grid.n_steps
    dt = np.diff(grid.t_years, prepend=grid.t_years[0])
    dt[0] = 0.0
    dt = np.maximum(dt, 0.0)

    if risk_neutral:
        mu = mp.rate - mp.div_yield
    else:
        mu = mp.drift if mp.drift is not None else (mp.rate - mp.div_yield)

    L = mp.chol()
    z = rng.standard_normal((n_paths, n_steps - 1, n_assets))
    z = z @ L.T

    drift_term = (mu - 0.5 * mp.vol**2)[None, None, :] * dt[1:][None, :, None]
    diff_term = mp.vol[None, None, :] * np.sqrt(dt[1:])[None, :, None] * z
    logpath = np.concatenate(
        [np.zeros((n_paths, 1, n_assets)), np.cumsum(drift_term + diff_term, axis=1)], axis=1
    )
    return mp.spot[None, None, :] * np.exp(logpath)


@dataclass
class PathResults:
    """一批路徑的判定結果（未含配息金額）。"""

    scenario: np.ndarray        # int, 見 AUTOCALL/NO_KI/... 常數
    exit_step: np.ndarray       # int
    exit_t: np.ndarray          # float, 自發行日起算年數
    redemption: np.ndarray      # float, 以面額為 1.0 的贖回價值
    worst_perf: np.ndarray      # float
    worst_asset: np.ndarray     # int
    ki_hit: np.ndarray          # bool


def _evaluate_paths(terms: FCNTerms, grid: StepGrid, paths: np.ndarray) -> PathResults:
    """向量化的給付判定，邏輯與 :func:`fcn.engine.evaluate` 一致。"""
    n_paths, n_steps, _ = paths.shape
    s0 = paths[:, 0, :]

    # ① 記憶事件 / ② 提前出場
    has_ac = np.zeros(n_paths, dtype=bool)
    ac_step = np.full(n_paths, grid.fvd_step, dtype=int)
    if terms.ko_type is not KOType.NONE and grid.ko_mask.any():
        hit = paths >= (s0 * terms.autocall_pct)[:, None, :]
        hit &= grid.ko_mask[None, :, None]
        state = np.maximum.accumulate(hit, axis=1) if terms.ko_type.has_memory else hit
        all_hit = state.all(axis=2)
        has_ac = all_hit.any(axis=1)
        ac_step = np.where(has_ac, all_hit.argmax(axis=1), grid.fvd_step)

    # ③ 觸及生效
    if terms.ki_type is KIType.NONE:
        ki_hit = np.ones(n_paths, dtype=bool)
    elif grid.ki_mask.any():
        breach = paths < (s0 * terms.ki_pct)[:, None, :]
        breach &= grid.ki_mask[None, :, None]
        ki_hit = breach.any(axis=(1, 2))
    else:
        ki_hit = np.zeros(n_paths, dtype=bool)

    # ④/⑤ 期末比價
    perf = paths[:, grid.fvd_step, :] / s0
    worst_asset = perf.argmin(axis=1)
    worst_perf = perf.min(axis=1)

    deliver = (~has_ac) & ki_hit & (worst_perf < terms.strike_pct)

    scenario = np.full(n_paths, ABOVE_STRIKE, dtype=np.int8)
    scenario[~has_ac & ~ki_hit] = NO_KI
    scenario[deliver] = DELIVERY
    scenario[has_ac] = AUTOCALL

    exit_step = np.where(has_ac, ac_step, grid.fvd_step)

    # 還本情境：Upside FCN 參與最差標的相對「參與表現價」的漲幅（以出場日表現計）
    redemption = np.ones(n_paths, dtype=float)
    if terms.is_upside:
        exit_px = np.take_along_axis(paths, exit_step[:, None, None], axis=1)[:, 0, :]
        worst_at_exit = (exit_px / s0).min(axis=1)
        redemption += terms.participation * np.maximum(
            0.0, worst_at_exit - terms.lower_call_strike_pct
        )

    # 承接時的價值 = 面額 x (最差表現 / 執行價%)
    redemption[deliver] = worst_perf[deliver] / terms.strike_pct
    return PathResults(
        scenario=scenario,
        exit_step=exit_step,
        exit_t=grid.t_years[exit_step],
        redemption=redemption,
        worst_perf=worst_perf,
        worst_asset=worst_asset,
        ki_hit=ki_hit,
    )


def _coupon_factors(terms: FCNTerms, grid: StepGrid, res: PathResults, rate: float) -> tuple:
    """回傳 (每單位配息率的年金現值, 每單位配息率的名目配息總額)。"""
    per_year = terms.coupon_freq.per_year
    ct = grid.coupon_t
    df_c = np.exp(-rate * np.maximum(ct, 0.0))

    exit_t = res.exit_t
    is_ac = res.scenario == AUTOCALL
    # 持有到期者配滿所有期數；提前出場者只配到出場日為止（末期以累計計息補足）。
    # 期末評價日（交易日 + 天期）可能略早於最後一期配息日（發行日 + 天期），
    # 該期仍於到期交割時支付，故不可用 exit_t 過濾。
    paid = np.where(is_ac[:, None], ct[None, :] <= exit_t[:, None] + 1e-12, True)
    n_paid = paid.sum(axis=1)

    annuity = (paid * (df_c / per_year)[None, :]).sum(axis=1)
    nominal = n_paid / per_year

    if terms.autocall_coupon is AutocallCoupon.ACCRUED:
        accrued_total = np.maximum(exit_t, 0.0)             # 每單位配息率的 ACT/365 累計
        stub = accrued_total - nominal
        stub = np.where(res.scenario == AUTOCALL, np.maximum(stub, 0.0), 0.0)
        annuity = annuity + stub * np.exp(-rate * np.maximum(exit_t, 0.0))
        nominal = nominal + stub

    return annuity, nominal


# --------------------------------------------------------------------------
# 對外介面
# --------------------------------------------------------------------------


@dataclass
class PricingResult:
    fair_coupon_pa: float
    quoted_coupon_pa: float | None
    pv_at_quote: float | None            # 以券商報價計算的理論價值（面額 = 1.0）
    scenario_probs: dict[str, float]
    prob_ki: float
    prob_delivery: float
    expected_life_years: float
    n_paths: int
    market: MarketParams
    schedule: Schedule

    @property
    def value_gap_pct(self) -> float | None:
        """券商報價相對公允價值的差距（負值 = 投資人吃虧）。"""
        return None if self.pv_at_quote is None else self.pv_at_quote - 1.0


@dataclass
class ForecastResult:
    returns: np.ndarray                  # 各路徑的總報酬率
    scenario: np.ndarray
    worst_perf: np.ndarray
    buy_hold_returns: np.ndarray         # 同期直接持有最差標的的報酬
    coupon_pa: float
    scenario_probs: dict[str, float] = field(default_factory=dict)

    def summary(self) -> dict[str, float]:
        r = self.returns
        return {
            "平均報酬": float(r.mean()),
            "中位數報酬": float(np.median(r)),
            "標準差": float(r.std(ddof=1)),
            "勝率": float((r > 0).mean()),
            "5% VaR": float(np.percentile(r, 5)),
            "1% VaR": float(np.percentile(r, 1)),
            "最差": float(r.min()),
            "最佳": float(r.max()),
            "對照:直接持有最差標的平均報酬": float(self.buy_hold_returns.mean()),
        }


def _run(
    terms: FCNTerms,
    mp: MarketParams,
    grid: StepGrid,
    n_paths: int,
    seed: int,
    *,
    risk_neutral: bool,
    chunk: int = 20_000,
):
    rng = np.random.default_rng(seed)
    chunks = []
    done = 0
    while done < n_paths:
        m = min(chunk, n_paths - done)
        paths = _simulate_chunk(mp, grid, m, rng, risk_neutral=risk_neutral)
        chunks.append((_evaluate_paths(terms, grid, paths), paths[:, grid.fvd_step, :]))
        done += m

    res = PathResults(
        scenario=np.concatenate([c[0].scenario for c in chunks]),
        exit_step=np.concatenate([c[0].exit_step for c in chunks]),
        exit_t=np.concatenate([c[0].exit_t for c in chunks]),
        redemption=np.concatenate([c[0].redemption for c in chunks]),
        worst_perf=np.concatenate([c[0].worst_perf for c in chunks]),
        worst_asset=np.concatenate([c[0].worst_asset for c in chunks]),
        ki_hit=np.concatenate([c[0].ki_hit for c in chunks]),
    )
    return res


def _scenario_probs(scenario: np.ndarray) -> dict[str, float]:
    return {
        label: float((scenario == code).mean())
        for code, label in enumerate(_SCENARIO_LABELS)
    }


def price(
    terms: FCNTerms,
    mp: MarketParams,
    *,
    trade_date: pd.Timestamp,
    n_paths: int = 100_000,
    seed: int = 20260809,
) -> PricingResult:
    """風險中性定價：反推公允年化配息率（並可評估券商報價是否合理）。"""
    cal = future_trading_days(trade_date, terms.tenor_months + 2)
    schedule = build_schedule(terms, cal, trade_date)
    grid = build_grid(terms, schedule, cal)

    res = _run(terms, mp, grid, n_paths, seed, risk_neutral=True)
    annuity, _ = _coupon_factors(terms, grid, res, mp.rate)

    df_exit = np.exp(-mp.rate * np.maximum(res.exit_t, 0.0))
    pv_redemption = float((res.redemption * df_exit).mean())
    pv_annuity = float(annuity.mean())

    fair = (1.0 - pv_redemption) / pv_annuity if pv_annuity > 0 else float("nan")

    pv_at_quote = None
    if terms.coupon_pa is not None:
        pv_at_quote = pv_redemption + terms.coupon_pa * pv_annuity

    return PricingResult(
        fair_coupon_pa=fair,
        quoted_coupon_pa=terms.coupon_pa,
        pv_at_quote=pv_at_quote,
        scenario_probs=_scenario_probs(res.scenario),
        prob_ki=float(res.ki_hit.mean()),
        prob_delivery=float((res.scenario == DELIVERY).mean()),
        expected_life_years=float(res.exit_t.mean()),
        n_paths=n_paths,
        market=mp,
        schedule=schedule,
    )


def forecast(
    terms: FCNTerms,
    mp: MarketParams,
    *,
    trade_date: pd.Timestamp,
    n_paths: int = 100_000,
    seed: int = 20260809,
) -> ForecastResult:
    """真實測度下的損益分布預測（需先設定 ``terms.coupon_pa``）。"""
    if terms.coupon_pa is None:
        raise ValueError("forecast() 需要 terms.coupon_pa；請先呼叫 price() 取得公允配息率")

    cal = future_trading_days(trade_date, terms.tenor_months + 2)
    schedule = build_schedule(terms, cal, trade_date)
    grid = build_grid(terms, schedule, cal)

    res = _run(terms, mp, grid, n_paths, seed, risk_neutral=False)
    _, nominal = _coupon_factors(terms, grid, res, mp.rate)

    total = res.redemption + terms.coupon_pa * nominal
    return ForecastResult(
        returns=total - 1.0,
        scenario=res.scenario,
        worst_perf=res.worst_perf,
        buy_hold_returns=res.worst_perf - 1.0,
        coupon_pa=terms.coupon_pa,
        scenario_probs=_scenario_probs(res.scenario),
    )
