# FCN 境外結構型商品 報價 / 模擬 / 回測工具

依台灣券商（永豐金、元大、中信、華南永昌）的產品說明與內部教育訓練資料，
把 **FCN（Fixed Coupon Note，固定配息提前出場境外結構型商品）** 的條款規則
完整程式化，並串接 Yahoo Finance 實際美股報價，進行：

1. **詢價（quote）** — 對報價單上留白的任一欄位求解（配息／執行價／下限價／
   提前出場價／參與表現價／通路費），並檢驗券商報價是否合理
2. **模擬（forecast）** — 真實機率測度下的損益分布與各情境機率
3. **回測（backtest）** — 同一組條件套用到歷史上每個進場日的實際結果
4. **逐筆判定（path）** — 單一進場日的記憶事件、KI 事件、期末比價明細
5. **對帳（reconcile）** — 拿真實 Term Sheet 逐項驗證模型結果

全部功能都有網頁介面（無額外相依，標準函式庫實作）。

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

## 網頁介面

```bash
python -m fcn.cli serve          # http://127.0.0.1:8000
python -m fcn.webapp --port 9000 --host 0.0.0.0
```

四個分頁：

| 分頁 | 內容 |
|---|---|
| **FCN 是什麼** | 給投資人的教學頁：一分鐘看懂、三個核心優點、**互動試算**（拉滑桿即時比較 FCN 與直接持股）、四種結局、高息從何而來、適用與不適用的行情、以及完整風險揭露 |
| **詢價與模擬** | 留白欄位求解、公允定價與價值差距分解、契約日程與各標的價位、市場參數校準、**到期損益曲線**、損益分布直方圖、情境機率 |
| **歷史回測** | 總覽指標、各情境明細、逐進場日報酬走勢（對照直接持有）、可捲動明細表 |
| **單筆路徑檢視** | **價格走勢圖**（含 KO／執行價／下限價／參與表現價水平線、記憶事件 ○ 與觸及生效事件 ✕ 標記）、現金流拆解、期末表現 |

上方的報價條件表比照券商詢價平台版面；**留白的欄位會以紅色虛線標示**，
表格下方會即時顯示「目前由系統試算哪一欄」。年化配息預設留白由系統試算，
也可直接填入券商報價來比對是否合理（「配息改由系統試算」按鈕可一鍵切回）。

連結標的可填 Bloomberg 代碼（`TSM UN`）或 Yahoo 代碼（`TSM`），輸入框附
常用標的建議清單，也可自行輸入任意代碼。按「查驗標的代碼」會即時解析並
顯示公司全名、幣別、最新收盤價與可用資料區間，並在標的跨幣別或跨交易所
時提出警告 —— 不必等跑完整個模擬才發現代碼打錯。

圖表為原生 SVG，不依賴任何前端函式庫，並支援深色模式。**折線圖可用十字線查價**：
游標移到圖上會鎖定最接近的資料點，列出各條線在該點的數值；價格走勢圖同時顯示
相對期初的表現與當日實際收盤價。

### 開放給內網同事使用

預設只綁 `127.0.0.1`，僅本機可連。要讓同一區域網路的同事使用，綁到所有介面：

```bash
python -m fcn.cli serve --host 0.0.0.0 --port 8000
```

啟動時會直接印出可分享的網址與警告：

```
FCN 詢價平台已啟動 →  http://192.168.1.23:8000
⚠ 已對外開放且未設定通行碼：任何連得到此位址的人都能使用。
  部署到公開網址時，請設定環境變數 FCN_TOKEN 啟用存取保護。
```

設了 `FCN_TOKEN` 之後改印 `🔒 已啟用通行碼保護`。內網若彼此信任可以不設；
放到公開網址則務必設定，詳見下一節。

背景常駐執行：

```bash
nohup python -m fcn.cli serve --host 0.0.0.0 --port 8000 > fcn-web.log 2>&1 &
```

或用 systemd（Linux）：

```ini
# /etc/systemd/system/fcn-web.service
[Unit]
Description=FCN 詢價平台
After=network.target

[Service]
User=fcn
WorkingDirectory=/opt/FCN
ExecStart=/usr/bin/python3 -m fcn.cli serve --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now fcn-web
```

### 部署到 Zeabur / Railway / Render

專案已備妥 `main.py`、`requirements.txt` 與 `zbpack.json`，推上 Git 後平台即可
自動辨識為 Python 專案並啟動。PaaS 以 `PORT` 環境變數指派通訊埠，
`resolve_bind()` 偵測到就會自動綁 `0.0.0.0`，不需額外設定。

**部署到公開網址前，請先設定通行碼：**

| 環境變數 | 說明 |
|---|---|
| `FCN_TOKEN` | 設定後啟用通行碼保護；未設定則**任何人拿到網址都能用** |
| `PORT` | 由平台自動注入，不需手動設定 |
| `HOST` | 選填，預設在有 `PORT` 時為 `0.0.0.0` |

Zeabur 的設定步驟：

1. 新增服務 → 選擇這個 Git repo 與分支
2. 進入服務的「環境變數」，新增 `FCN_TOKEN`，值自己取一組夠長的字串
3. 綁定網域後即可使用；第一次開啟會要求輸入通行碼

通行碼機制刻意做得很簡單：驗證通過後寫入 `HttpOnly`、`SameSite=Strict` 的
Cookie（在 HTTPS 下加 `Secure`），比對用 `hmac.compare_digest` 避免時序側漏。
`/api/health` 不需通行碼，供平台健康檢查使用。

這是一道**防止路人誤用的門，不是嚴謹的身分驗證系統**：沒有帳號、沒有分權、
沒有稽核紀錄。真的需要那些，請放在有身分驗證的反向代理之後。

#### 記憶體

蒙地卡羅的暫存陣列以 `(路徑 × 步階 × 標的)` 計算，很容易吃掉數百 MB。
路徑產生已改為就地運算，並將批次大小降到 5,000 條，讓 12 個月 / 4 檔標的
的模擬峰值控制在數百 MB 以內。若平台記憶體低於 1 GB，請把網頁上的
「模擬路徑數」設為 20,000。

#### 多人同時使用的注意事項

| 項目 | 說明 |
|---|---|
| **並行能力** | 蒙地卡羅是 CPU 密集運算。實測 40,000 條路徑約 5~6 秒；同時 3~5 人使用尚可，再多就會明顯排隊。人多時可把預設路徑數調到 20,000。 |
| **Yahoo 流量限制** | 所有人共用同一組對外 IP。行情已在記憶體中快取（同一組標的與日期區間只抓一次），但短時間大量查驗不同標的仍可能被暫時擋下（HTTP 429）。 |
| **磁碟快取** | `.cache/` 會累積行情 JSON，可隨時整個刪除，下次會自動重抓。 |
| **無狀態** | 服務不保存任何使用者資料，重啟即可，不需備份。 |

---

## 快速開始（命令列）

### 詢價：欲詢價的欄位留白

對應詢價平台「欲詢價參數請於該欄位留白」的操作。省略 `--coupon` 即為詢配息：

```bash
python -m fcn.cli quote \
    --ud "NVDA UW" --ud "TSM UN" --ud "AMD UW" \
    --tenor 12 --strike 80 --autocall 100 --ki 75 --ki-type AKI --rebate 3
```

改用 `--solve` 指定其他留白欄位（`coupon` / `rebate` / `strike` / `ki` /
`autocall` / `lower-call`）：

```bash
# 已知配息 22%，反解券商該給的執行價
python -m fcn.cli quote --ud "NVDA UW" --ud "TSM UN" --ud "AMD UW" \
    --coupon 22 --solve strike --ki 75 --ki-type AKI --rebate 3

# 已知全部條件，反解報價隱含的通路費空間
python -m fcn.cli quote --ud "NVDA UW" --ud "TSM UN" --ud "AMD UW" \
    --coupon 15 --solve rebate --strike 80 --ki 70 --ki-type AKI
```

`coupon` 與 `rebate` 有封閉解；其餘欄位以二分搜尋求解，並對所有候選值重用
同一批隨機路徑（common random numbers），使目標函數平滑且結果可重現。
若在合法區間內無解，會明確報告兩端的理論價值而非給出錯誤答案。

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

### 以真實 Term Sheet 對帳

把條款確認書的內容填成 JSON（格式見 `examples/termsheet_sample.json`），
逐項比對模型結果與文件記載：

```bash
python -m fcn.cli reconcile examples/termsheet_sample.json
```

```
  項目                     Term Sheet          模型   結果
  價格尺度 NVDA                   x10           x10   PASS
  期初價 NVDA                309.4500      309.4500   PASS
  情境                       到期承接股票      到期承接股票   PASS
  承接價                      247.5600      247.5600   PASS
  承接股數                     403.0000      403.0000   PASS
  比對 13 項，13 項相符，全部通過 ✓
```

`levels` 區塊填文件載明的**絕對價位**，優先於「期初價 × 百分比」——
真實條款的執行價與下限價多半經過四捨五入。`expected` 區塊填已知的實際結果，
可只填其中幾項；未填的項目會標示為「—」而不參與判定。
未通過時 exit code 為 1，方便接進 CI。

#### 股票分割

Yahoo 的收盤價已還原分割，但 Term Sheet 記載的是發行當時的原始報價。
工具會比對兩者推得倍數，若落在實際存在的分割比例上（1、10、3/2、1/3 …）
就把**行情序列還原成文件的價格尺度**再計算。

方向不能反過來：整股交割是在原始價格上取整的，
`floor(100,000 / 247.56) = 403` 股與 `floor(100,000 / 24.756) = 4,039` 股
並非 10 倍關係，換算錯邊會算出不同的零股找補與總價值。

倍數若不是乾淨的分割比例（例如相差 3.7%），工具**不會**自動縮放，
而是讓期初價比對把差異顯示出來 —— 那通常代表交易日填錯或標的代碼有誤。

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
| **Rebate 行銷通路費** | `--rebate` | 0 | % — 一次性，由面額中先扣 |

### 詢價平台可受理範圍

| 欄位 | 範圍 |
|---|---|
| 連結標的 | 1~4 檔 |
| 產品天期 | 2~12 個月 |
| 執行價 | 50%~100% |
| 提前出場價 | 90%~120% |
| 下限價 | 不低於 50%（無下限則設 NA） |
| 行銷通路費 | 0.2%~3% |

超出範圍不會被程式擋下（分析用途仍可計算），但報表會標示 `⚠ 超出詢價平台可受理範圍`。

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
無需疊代求根。投資人付出面額 100%，其中行銷通路費先被抽走：

```
PV_贖回 + c × A = 100% − Rebate        A = 每單位配息率的年金現值
=>  c = (100% − Rebate − PV_贖回) / A
```

報價的價值差距可以分解：

| 數值 | 意義 |
|---|---|
| `pv_at_quote` | 以券商報價計算，投資人取得現金流的現值 |
| `value_gap_pct` | `pv_at_quote − 100%`，負值 = 投資人吃虧 |
| `implied_total_fee` | 報價隱含的總抽成（發行商利潤 + 通路費） |
| `issuer_margin` | 扣掉報價單載明的通路費後，發行商保留的部分 |

---

## 專案結構

```
fcn/
├── terms.py      報價條件（FCNTerms）與列舉型別
├── schedule.py   契約日程：交易日/發行日/KO・KI 觀察期間/配息日/期末評價日
├── engine.py     給付規則引擎（單一路徑，參考實作）
├── mc.py         向量化蒙地卡羅：留白欄位求解 + 公允定價 + 損益分布預測
├── backtest.py   歷史回測
├── data.py       Yahoo Finance 行情擷取 + Bloomberg 代碼對應
├── mktcal.py     NYSE 交易日曆
├── reconcile.py  Term Sheet 對帳（絕對價位 + 分割偵測）
├── report.py     文字報表
├── cli.py        命令列介面
├── webapp.py     網頁介面（http.server + JSON API）
└── web/          前端（index.html / style.css / app.js，原生 SVG 繪圖）
tests/            210 項測試
```

`engine.py`（可讀的單路徑實作）與 `mc.py`（向量化實作）在
`tests/test_mc.py`、`tests/test_upside.py` 中對同一批隨機路徑逐條交叉驗證，
確保情境、贖回金額、出場日、配息完全一致。

```bash
python -m pytest tests/ -q
```

前端另有兩道防線：`node --check` 驗證 `app.js` 語法（有裝 node 才執行），
以及比對 `app.js` 取用的表單 id 是否都存在於 `index.html`。這兩類問題會讓
整個頁面靜默失效，很難靠肉眼發現。

---

## 免責

本工具為條款模擬與教育用途，**非投資建議**。實際給付以發行機構之產品說明書
與條款確認書為準。FCN 屬不保本商品，產品風險等級 RR4~RR5，僅限專業投資人投資。

模型侷限：採用常數波動度的 GBM，未納入波動度微笑／傾斜、跳躍風險、
發行商信用價差，因此「公允配息率」是**理論上限的參考值**，實務報價必然低於
此數；差距多寡才是有意義的比較基準。報價單上的行銷通路費（Rebate）已納入
計算，但發行商本身的避險成本與利潤未單獨建模，會一併落在 `issuer_margin`。
