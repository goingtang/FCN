"""教學頁測試。

除了結構檢查，最重要的是**教學頁的互動試算不可與真實引擎矛盾** —— 教學素材
講錯規則比程式出錯更難發現，也更容易誤導人。此處以 :mod:`fcn.engine` 重算
頁面上宣稱的數字，規則若變動就會讓測試失敗。
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from fcn import webapp
from fcn.engine import Scenario, evaluate
from fcn.schedule import build_schedule
from fcn.terms import FCNTerms, KIType, KOType

HTML = (webapp.WEB_DIR / "index.html").read_text()
JS = (webapp.WEB_DIR / "app.js").read_text()
CSS = (webapp.WEB_DIR / "style.css").read_text()


# --------------------------------------------------------------------------
# 結構
# --------------------------------------------------------------------------


def test_learn_tab_is_the_landing_page():
    """新手應該先看到教學頁，而不是一張報價條件表。"""
    nav = re.search(r"<nav>(.*?)</nav>", HTML, re.S).group(1)
    buttons = re.findall(r'data-tab="([\w-]+)"([^>]*)>', nav)
    assert buttons[0][0] == "learn"
    assert 'class="on"' in buttons[0][1]
    assert 'id="tab-learn"' in HTML


def test_terms_panel_is_hidden_on_the_learn_tab():
    """教學頁不該出現報價條件表，否則第一眼就勸退。"""
    assert 'id="terms-panel"' in HTML
    assert "$('terms-panel').style.display" in JS


@pytest.mark.parametrize(
    "eid",
    ["s-cpn", "s-strike", "s-ki", "s-final", "s-breach",
     "l-cpn", "l-strike", "l-ki", "l-final", "l-buffer",
     "v-scenario", "v-fcn", "v-hold", "v-delta", "v-chart",
     "v-fcn-detail", "v-hold-detail", "btn-goto-quote", "btn-png"],
)
def test_interactive_elements_exist(eid):
    assert f'id="{eid}"' in HTML, f"教學頁缺少元素 {eid}"


def test_learn_styles_do_not_leak_into_other_tabs():
    """`.panel > p` 權重高於 `.hint`，未限縮會撐大其他分頁的提示文字。"""
    assert "#tab-learn .panel > p" in CSS
    assert re.search(r"^\.panel > p \{", CSS, re.M) is None


# --------------------------------------------------------------------------
# 均衡揭露
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "must",
    ["不保本", "報酬封頂", "承接的是最差那檔", "流動性差",
     "發行機構信用風險", "匯率風險", "費用不透明", "RR4~RR5", "專業投資人"],
)
def test_risk_disclosure_is_present(must):
    """行銷性質的教學頁必須均衡揭露風險，這是境外結構型商品的基本要求。"""
    assert must in HTML, f"教學頁缺少風險揭露：{must}"


def test_page_states_where_the_yield_comes_from():
    """高配息的來源必須說清楚，否則就是在賣「天上掉下來的錢」。"""
    assert "權利金" in HTML
    assert "報酬有上限" in HTML or "報酬封頂" in HTML
    assert "最差" in HTML          # worst-of 的反分散特性


def test_page_states_when_not_to_buy():
    assert "這些情況不要碰" in HTML
    for case in ("看好它會大漲", "不願意持有", "短期內要用", "保本"):
        assert case in HTML


# --------------------------------------------------------------------------
# 試算結果必須與真實引擎一致
# --------------------------------------------------------------------------


def _engine_return(strike: float, ki: float, coupon: float, final: float, breach: bool) -> float:
    """用真實引擎跑出教學頁該顯示的到期報酬（期初價 = 100）。"""
    terms = FCNTerms(
        underlyings=["AAA"],
        tenor_months=12,
        coupon_pa=coupon,
        strike_pct=strike,
        ko_type=KOType.NONE,          # 教學頁的滑桿聚焦到期情境
        autocall_pct=1.0,
        ki_type=KIType.AKI,
        ki_pct=ki,
        notional=100_000.0,
        integer_shares=False,
    )
    days = pd.bdate_range("2024-01-02", periods=400, name="date")
    px = pd.DataFrame(100.0 * strike + 1.0, index=days, columns=["AAA"], dtype=float)
    px.iloc[0] = 100.0
    sch = build_schedule(terms, days, days[0])
    if breach:
        px.loc[sch.ki_days[30]] = 100.0 * ki - 0.01
    px.loc[sch.final_valuation] = 100.0 * final
    return evaluate(terms, px, sch).return_pct


@pytest.mark.parametrize(
    "final,breach,expected,scenario_word",
    [
        (1.00, False, 0.12, "還本"),          # 平盤未破 KI -> 只拿配息
        (0.70, False, 0.12, "還本"),          # 低於執行價但未破 KI -> 仍全額還本
        (0.70, True, -0.005, "承接"),         # 破 KI 且低於執行價 -> 承接 70/80-1+12%
        (1.40, True, 0.12, "還本"),           # 破 KI 但站回執行價之上 -> 還本
        (0.40, True, -0.38, "承接"),          # 重挫 40/80-1+12%
    ],
)
def test_learn_simulator_matches_engine(final, breach, expected, scenario_word):
    got = _engine_return(0.80, 0.60, 0.12, final, breach)
    assert got == pytest.approx(expected, abs=1e-9), (
        f"教學頁宣稱 {expected:.2%}，引擎算出 {got:.2%}"
    )


def test_learn_simulator_formula_in_js_matches_engine():
    """JS 端的給付公式必須與引擎一致。

    只比對不變式，不比對標點：綁死字面寫法會讓單純的重構誤報。
    """
    assert "function learnOutcome" in JS
    # 承接：面額 x (期末 / 執行價)
    assert re.search(r"NOTIONAL\s*\*\s*\(\s*final\s*/\s*strike\s*\)", JS)
    # 未承接：報酬就是配息率，沒有其他上檔
    assert re.search(r"\bret:\s*cpn\b", JS)


@pytest.mark.parametrize("ki_type", [KIType.AKI, KIType.EKI])
def test_final_price_below_the_barrier_is_always_a_breach(ki_type):
    """期末價低於下限價卻顯示「還本」是不可能的情境。

    AKI 逐日觀察會碰到，EKI 的期末觀察也會碰到 —— 引擎兩種都判定承接，
    教學頁的勾選框因此不能讓人選出「未破」。
    """
    terms = FCNTerms(
        underlyings=["AAA"], tenor_months=12, coupon_pa=0.12, strike_pct=0.80,
        ko_type=KOType.NONE, autocall_pct=1.0, ki_type=ki_type, ki_pct=0.60,
        notional=100_000.0, integer_shares=False,
    )
    days = pd.bdate_range("2024-01-02", periods=400, name="date")
    px = pd.DataFrame(100.0, index=days, columns=["AAA"], dtype=float)
    sch = build_schedule(terms, days, days[0])
    px.loc[sch.final_valuation] = 20.0       # 只有期末那天在下限價之下
    assert evaluate(terms, px, sch).scenario is Scenario.DELIVERY


def test_js_treats_a_sub_barrier_final_price_as_a_breach():
    """給付、對照表、損益圖三處都要套同一條規則，否則會互相矛盾。"""
    assert re.search(r"const hit = breached \|\| final < ki", JS)
    assert JS.count("breached || v < ki") + JS.count("breached || x < ki") >= 2


@pytest.mark.parametrize("eid", ["s-breach-note", "v-table-note"])
def test_breach_explanations_exist(eid):
    assert f'id="{eid}"' in HTML


def test_breach_checkbox_is_locked_below_the_barrier():
    """鎖住之後必須說明原因，並記住使用者原本的選擇才還原得了。"""
    assert re.search(r"const forced = final < ki", JS)
    assert re.search(r"box\.disabled = forced", JS)
    assert "userBreach" in JS
    assert "無法選擇「未破」" in JS


def test_impossible_table_cells_are_left_blank():
    """低於下限價那幾列的「未破下限價」填數字等於在示範不存在的情境。"""
    assert re.search(r"x < ki \?\s*el\('span', \{ class: 'na'", JS)
    assert "不可能成立" in JS


def test_ki_slider_cannot_exceed_strike():
    """下限價必須低於執行價，否則會示範出不存在的條款。

    檢查夾制邏輯存在，且兩支滑桿都接上 —— 只接一支的話，拉另一支就會跑掉。
    """
    assert re.search(
        r"\$\('s-ki'\)\.value\s*=\s*\+\$\('s-strike'\)\.value\s*-\s*1", JS)
    for slider in ("s-strike", "s-ki"):
        assert re.search(
            rf"\$\('{slider}'\)\.addEventListener\('input',\s*clampKi\)", JS), (
            f"{slider} 未接上下限價夾制"
        )


# --------------------------------------------------------------------------
# 比較必須看得出差異
# --------------------------------------------------------------------------


def test_default_scenario_is_not_a_degenerate_comparison():
    """預設若停在股價 100%，「直接買股票」永遠是 0%，看起來像沒算出來。"""
    m = re.search(r'<input type="range" id="s-final"[^>]*value="(\d+)"', HTML)
    assert m, "找不到到期股價滑桿"
    assert m.group(1) != "100", "預設到期股價不應停在 100%（兩側對比會退化成 0%）"


def test_controls_are_split_into_product_and_market_groups():
    """商品條件動不到股票側；不分組會讓人以為股票那欄壞掉。"""
    assert "① 商品條件" in HTML and "② 市場情境" in HTML
    assert "只由這一組決定" in HTML


@pytest.mark.parametrize("eid", ["v-fcn-amt", "v-hold-amt", "v-table", "v-ko-note"])
def test_amount_and_table_elements_exist(eid):
    assert f'id="{eid}"' in HTML


def test_table_shows_both_barrier_states():
    """對照表要同時列出未破與已破下限價，才不必來回勾選才看得懂。"""
    assert "FCN（未破下限價）" in JS and "FCN（曾破下限價）" in JS
    assert "TABLE_ROWS" in JS


def test_row_highlight_tie_break_is_deterministic():
    """130% 正好落在 120% 與 140% 中間，不加容差會由浮點誤差決定標記哪一列。"""
    assert "Math.abs(x - final) + 1e-9 < Math.abs(near - final)" in JS


def test_page_warns_that_autocall_is_not_modelled():
    """股價漲回原點時真實商品多半已提前出場，教學頁必須說明此簡化。"""
    assert "提前出場價多半訂在期初的 100%" in JS


@pytest.mark.parametrize(
    "final,breach,expected",
    [
        (0.40, True, -0.38),      # 40/80-1+12%
        (0.50, True, -0.255),
        (0.70, True, -0.005),
        (0.80, True, 0.12),       # 恰好等於執行價 -> 還本
        (1.20, False, 0.12),      # 報酬封頂在配息
    ],
)
def test_table_rows_match_engine(final, breach, expected):
    assert _engine_return(0.80, 0.60, 0.12, final, breach) == pytest.approx(expected, abs=1e-9)


# --------------------------------------------------------------------------
# 用真實股價試算
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "eid",
    ["s-ticker", "btn-load-stock", "quick-picks", "s-info",
     "l-strike-px", "l-ki-px", "l-final-pct", "l-coupon-amt", "l-table-sym"],
)
def test_stock_picker_elements_exist(eid):
    assert f'id="{eid}"' in HTML, f"教學頁缺少標的選擇元素 {eid}"


def test_price_levels_are_shown_in_dollars():
    """「執行價 80%」不如「跌到 174.05 才要接手」直覺。"""
    assert "l-strike-px" in JS and "l-ki-px" in JS
    assert re.search(r"l-strike-px'\)\.textContent\s*=\s*num\(spot \* strike\)", JS)
    assert re.search(r"l-ki-px'\)\.textContent\s*=\s*num\(spot \* ki\)", JS)


def test_falls_back_to_a_placeholder_price():
    """行情抓不到時仍要能操作，不能整頁卡住。"""
    assert re.search(r"price:\s*100,\s*live:\s*false", JS)
    assert "示意價試算" in JS


def test_delivery_shows_share_count_with_a_decimal():
    """100,000 / 174.05 = 574.5；四捨五入成 575 會讓人對不上數字。"""
    assert re.search(r"num\(r\.shares,\s*1\)", JS)
    assert re.search(r"num\(NOTIONAL / spot,\s*1\)", JS)


def test_ticker_picker_is_wired_to_the_shared_datalist():
    m = re.search(r'<input id="s-ticker"[^>]*>', HTML)
    assert m and 'list="ticker-list"' in m.group(0)


def test_key_amount_lines_are_at_least_16px():
    """使用者指定：金額那兩行的字級不得低於 16px。"""
    for sel in (r"\.side \.sub", r"\.side \.amt"):
        block = re.search(rf"{sel} \{{(.*?)\}}", CSS, re.S)
        assert block, f"找不到 {sel} 的樣式"
        size = re.search(r"font-size:\s*(\d+)px", block.group(1))
        assert size and int(size.group(1)) >= 16, f"{sel} 字級小於 16px"


# --------------------------------------------------------------------------
# 分享圖（PNG 下載）
# --------------------------------------------------------------------------

SHARE_JS = JS[JS.index("/* ====================== 分享圖"):]


def test_share_card_is_wired_to_the_button():
    assert "function buildShareSvg" in SHARE_JS
    assert re.search(r"\$\('btn-png'\)\.addEventListener\('click',\s*downloadSharePng\)", JS)


def test_share_card_does_not_follow_the_dark_theme():
    """匯出圖若跟著深色模式跑，同一組條件分享出去會長得不一樣。"""
    assert "css(" not in SHARE_JS, "分享圖不應讀取 CSS 主題變數"
    assert re.search(r"CARD\s*=\s*\{", JS)


def test_share_card_avoids_foreign_object():
    """foreignObject 在 canvas 光柵化時不保證會畫出來，只能用 <text>。"""
    assert "svg('foreignObject'" not in JS and "<foreignObject" not in JS


def test_share_card_carries_the_risk_disclosure():
    """圖被分享出去就脫離了網頁脈絡，風險揭露必須印在圖上。"""
    for must in ("不保本", "封頂", "不構成投資建議", "信用風險"):
        assert must in SHARE_JS, f"分享圖缺少揭露：{must}"


def test_share_card_payoff_matches_the_page():
    """卡片上的損益線必須與 learnOutcome 同一條規則。

    承接的條件是「觸發過下限價」且「期末低於執行價」，其中期末價本身低於
    下限價也算觸發 —— 三處若不一致，圖與數字就會互相打架。
    """
    assert re.search(
        r"\(breached \|\| v < ki\)\s*&&\s*v < strike\s*\?\s*v / strike - 1\s*:\s*0", SHARE_JS)


def test_share_file_name_is_identifiable():
    assert re.search(r"`FCN_\$\{s\}_", SHARE_JS)
    assert ".png" in SHARE_JS


def test_png_is_rendered_on_an_opaque_background():
    """PNG 有透明通道；不先鋪底色，貼到深色簡報上文字會看不見。"""
    assert "fillRect" in SHARE_JS
    assert re.search(r"ctx\.fillStyle\s*=\s*CARD\.bg", SHARE_JS)


def test_png_is_exported_above_screen_resolution():
    assert re.search(r"const scale\s*=\s*[2-4]\b", SHARE_JS)
    assert re.search(r"cv\.width\s*=\s*CARD\.w \* scale", SHARE_JS)
