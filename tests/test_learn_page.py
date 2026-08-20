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
     "v-fcn-detail", "v-hold-detail", "btn-goto-quote"],
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


def test_ki_slider_cannot_exceed_strike():
    """下限價必須低於執行價，滑桿要互相夾住，否則會示範出不存在的條款。"""
    assert JS.count("$('s-ki').value = +$('s-strike').value - 1") >= 2


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
