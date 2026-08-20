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
    """JS 端的承接公式必須是 面額 x (期末 / 執行價)，與引擎一致。"""
    assert "NOTIONAL * (final / strike)" in JS
    # 未承接時報酬就是配息率，且沒有其他上檔
    assert "ret = cpn;" in JS


def test_ki_slider_cannot_exceed_strike():
    """下限價必須低於執行價，滑桿要互相夾住，否則會示範出不存在的條款。"""
    assert JS.count("$('s-ki').value = +$('s-strike').value - 1") >= 2
