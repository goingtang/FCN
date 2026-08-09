# FCN 境外結構型商品 報價 / 模擬 / 回測工具

依台灣券商（永豐金、元大、中信、華南永昌）的產品說明與內部教育訓練資料，
把 **FCN（Fixed Coupon Note，固定配息提前出場境外結構型商品）** 的條款規則
完整程式化，並串接 Yahoo Finance 實際美股報價，進行：

1. **報價（quote）** — 反推公允年化配息率，檢驗券商報價是否合理
2. **模擬（forecast）** — 真實機率測度下的損益分布與各情境機率
3. **回測（backtest）** — 同一組條件套用到歷史上每個進場日的實際結果
4. **逐筆判定（path）** — 單一進場日的記憶事件、KI 事件、期末比價明細

支援兩種商品架構：

| | **FCN** | **Upside FCN** |
|---|---|---|
| 中文名 | 固定配息提前出場境外結構型商品 | 可參與漲幅固定配息提前出場境外結構型商品 |
| 還本金額 | 面額 × 100% | 面額 × [100% + 參與率 × max(0%, 最差標的表現 − 參與表現價)] |
| 報酬上限 | 配息總額（封頂） | 無上限（但被提前出場截斷） |
| 專屬欄位 | — | Lower Call Strike（參與表現價）、參與率 |

---

## 安裝

```bash
pip install numpy pandas pytest
```

無需 `yfinance` — 行情直接走 Yahoo v8 chart API（`yfinance` 內建 session
在多數雲端 IP 會被 429 擋下）。

---

## 快速開始

### 反推公允配息率（Cpn p.a. 不是輸入值，是算出來的）

```bash
python -m fcn.cli quote \
    --ud "TSM UN" --ud "NVDA UW" --ud "MSFT UW" \
    --tenor 12 --strike 80 --autocall 100 --ki 60 --ki-type AKI \
    --coupon 12          # 選填：填入券商報價以比對價值差距
```

輸出包含市場參數校準（波動度、相關矩陣、股息殖利率）、公允配息率、
承接股票機率、以及報酬分布分位數。

### Upside FCN

```bash
python -m fcn.cli quote \
    --ud "TSM UN" --ud "AMD UW" --ud "NVDA UW" \
    --tenor 12 --coupon 9 --strike 85 \
    --ko-type Monthly --autocall 100 \
    --lower-call 103 --ki-type NA
```

### 歷史回測

```bash
python -m fcn.cli backtest \
    --ud "TSM UN" --ud "NVDA UW" --ud "MSFT UW" \
    --coupon 12 --years 6 --step 5 --csv out.csv
```

### 單一進場日的逐項判定

```bash
python -m fcn.cli path \
    --ud "TSM UN" --ud "NVDA UW" --ud "MSFT UW" \
    --coupon 12 --trade-date 2024-06-03
```

### 程式化使用

```python
import pandas as pd
from fcn import mc
from fcn.backtest import run_backtest
from fcn.data import load_market_data, fetch_risk_free_rate
from fcn.terms import FCNTerms, KOType, KIType

terms = FCNTerms(
    underlyings=["TSM UN", "NVDA UW", "MSFT UW"],
    tenor_months=12,
    coupon_pa=None,              # None -> 由模型反推
    strike_pct=0.80,
    ko_type=KOType.DAILY_MEMORY,
    autocall_pct=1.00,
    ki_type=KIType.AKI,
    ki_pct=0.60,
)

md = load_market_data(terms.underlyings, "2021-01-01", "2026-08-08")
ysyms = [md.symbol_map[u] for u in terms.underlyings]
mp = mc.calibrate(md.adjcloses[ysyms], rate=fetch_risk_free_rate(),
                  div_yields=md.dividend_yields())

pricing = mc.price(terms, mp, trade_date=md.trading_days[-1])
print(f"公允配息率 {pricing.fair_coupon_pa:.2%}，承接機率 {pricing.prob_delivery:.2%}")

bt = run_backtest(terms.with_coupon(pricing.fair_coupon_pa), md)
print(bt.summary())
```

---

## 報價條件欄位對照

CLI 參數與券商報價條件表一一對應：

| 報價條件表 | CLI 參數 | 預設 | 說明 |
|---|---|---|---|
| UD1~UD4 連結標的 | `--ud`（可重複 1~4 次） | 必填 | 支援 Bloomberg（`TSM UN`）或 Yahoo（`TSM`）代碼 |
| CCY 幣別 | `--ccy` | USD | |
| Tenor (M) 天期 | `--tenor` | 12 | 月 |
| Cpn p.a. 年化配息 | `--coupon` | 由模型反推 | % |
| I Delay 發行日 | `--i-delay` | 5 | 交易日 + N 個營業日 |
| Cpn Frequency 配息頻率 | `--freq` | Monthly | Monthly/Quarterly/Semiannual/Annual |
| Put Strike 執行價 | `--strike` | 80 | % |
| KO Type 提前出場型式 | `--ko-type` | Daily Memory | Daily Memory / Daily / Monthly Memory / Monthly / None |
| Autocall 提前出場價 | `--autocall` | 100 | % |
| KI Type 觸及生效型式 | `--ki-type` | AKI | AKI / EKI / NA |
| KI Level 下限價 | `--ki` | 60 | % |
| **Lower Call Strike 參與表現價** | `--lower-call` | 無 | % — 設定即成為 Upside FCN |
| **參與率** | `--participation` | 100 | % |

支援的 Bloomberg 交易所代碼：`UN`/`UW`/`UQ`/`UA`/`US`（美國）、`TT`（台灣）、
`TW`（櫃買）、`HK`（香港）、`JP`/`JT`（日本）、`SP`（新加坡）、`LN`（倫敦）、
`GR`（德國）、`FP`（法國）、`AU`（澳洲）、`CN`（加拿大）。

---

## 給付規則

判定順序完全依照永豐金證券「運作機制」流程圖：

```
① 記憶事件   KO 觀察期間內，任一標的收盤價 >= 提前出場價 -> 該標的記憶
② 提前出場   所有標的皆已記憶的當日 -> 領回 100% 本金 + 應計配息 (+ 漲幅)
③ 觸及生效   KI 觀察期間內，任一標的收盤價 < 下限價 -> 發生 KI 事件
             未發生 -> 到期領回 100% 本金 + 全額配息 (+ 漲幅)
④ 期末比價   已發生 KI，但期末所有標的收盤價 >= 執行價 -> 領回 100% 本金 (+ 漲幅)
⑤ 承接股票   否則以執行價承接【表現最差標的】
             股數 = 面額 / (最差標的期初價 × 執行價%)
```

### 觀察期間

| 事件 | 期間 |
|---|---|
| KO（提前出場） | 發行日 + 鎖定期（預設 1 個月） ~ 期末評價日**前**（不含） |
| KI — AKI（美式） | 交易日（含） ~ 期末評價日（含），每一預定交易日 |
| KI — EKI（歐式） | 僅期末評價日 |
| KI — NA（無 KI） | 視同恆已觸發，直接以期末最差標的 vs 執行價判定承接 |

### 已落實的四項條款註記

1. **記憶事件與觸及生效事件互為獨立** — 曾記憶之標的，到期若為最差表現且低於執行價，**仍須承接**
2. **承接標的為「表現最差」者**，不必然是觸發 KI 的那一檔
3. **觸發 KI 不等於立即承接**，仍須看期末評價日收盤價
4. **下限價必須低於執行價**（建構條款時即驗證）

### 邊界符號（不對稱，且會實質影響判定）

| 比較 | 符號 |
|---|---|
| 收盤價 vs 提前出場價 | `>=` |
| 收盤價 vs 下限價 | `<`（**嚴格**小於才算觸發） |
| 期末收盤價 vs 執行價 | `>=` |

### 記憶式 vs 非記憶式

- **Daily Memory / Monthly Memory**：各標的可在**不同觀察日**各自記憶，全數記憶後即提前出場
- **Daily / Monthly**：須於**同一觀察日**全部標的同時 `>=` 提前出場價

---

## 價格資料慣例

結構型商品的障礙比對，用的是**實際成交收盤價**，僅因分割等公司行為調整，
**不因除息調整**。本專案：

- **障礙判定（KO/KI/執行價）** 使用 Yahoo 的 `close`（已還原分割、未還原股息）
- **波動度與相關性校準** 使用 `adjclose`（總報酬）
- **股息殖利率** 由最近 12 個月實配現金股息推算，作為模擬的連續股息率

多標的商品的「預定交易日」取各標的交易日的**交集**。模擬未來路徑時使用
NYSE 假期日曆（`fcn/mktcal.py`），避免用週一至週五近似而高估觀察日數量。

---

## 定價方法

風險中性蒙地卡羅，多資產相關 GBM：

```
dS_i / S_i = (r − q_i) dt + σ_i dW_i,    corr(dW_i, dW_j) = ρ_ij
```

**提前出場與承接與否完全不受配息率影響**，因此票息腳的現值對配息率呈線性，
無需疊代求根：

```
PV = PV_贖回 + c × A          A = 每單位配息率的年金現值
令 PV = 面額（發行價 100%）  =>   c = (面額 − PV_贖回) / A
```

`PricingResult.value_gap_pct` 給出券商報價相對公允價值的差距（負值 = 投資人吃虧）。

---

## 專案結構

```
fcn/
├── terms.py      報價條件（FCNTerms）與列舉型別
├── schedule.py   契約日程：交易日/發行日/KO・KI 觀察期間/配息日/期末評價日
├── engine.py     給付規則引擎（單一路徑，參考實作）
├── mc.py         向量化蒙地卡羅：公允配息率反推 + 損益分布預測
├── backtest.py   歷史回測
├── data.py       Yahoo Finance 行情擷取 + Bloomberg 代碼對應
├── mktcal.py     NYSE 交易日曆
├── report.py     文字報表
└── cli.py        命令列介面
tests/            63 項測試
```

`engine.py`（可讀的單路徑實作）與 `mc.py`（向量化實作）在
`tests/test_mc.py`、`tests/test_upside.py` 中對同一批隨機路徑逐條交叉驗證，
確保情境、贖回金額、出場日、配息完全一致。

```bash
python -m pytest tests/ -q
```

---

## 免責

本工具為條款模擬與教育用途，**非投資建議**。實際給付以發行機構之產品說明書
與條款確認書為準。FCN 屬不保本商品，產品風險等級 RR4~RR5，僅限專業投資人投資。

模型侷限：採用常數波動度的 GBM，未納入波動度微笑／傾斜、跳躍風險、
發行商信用價差與各項費用，因此「公允配息率」是**理論上限的參考值**，
實務報價必然低於此數；差距多寡才是有意義的比較基準。
