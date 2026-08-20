"""標的代碼查驗端點與前端建議清單的一致性測試。"""

from __future__ import annotations

import re

import pytest

from fcn import webapp
from fcn.data import to_yahoo_symbol


def test_rejects_empty_and_oversized_lists():
    with pytest.raises(webapp.ApiError, match="至少輸入一檔"):
        webapp.api_tickers({"underlyings": []})
    with pytest.raises(webapp.ApiError, match="最多 4 檔"):
        webapp.api_tickers({"underlyings": ["A", "B", "C", "D", "E"]})


def test_datalist_entries_all_resolve():
    """建議清單裡的每個代碼都必須能轉成 Yahoo 代碼，否則使用者一選就壞。"""
    html = (webapp.WEB_DIR / "index.html").read_text()
    block = re.search(r'<datalist id="ticker-list">(.*?)</datalist>', html, re.S)
    assert block, "找不到 ticker-list 建議清單"

    values = re.findall(r'<option value="([^"]+)"', block.group(1))
    assert len(values) >= 20, f"建議清單只有 {len(values)} 筆，太少"

    for v in values:
        sym = to_yahoo_symbol(v)
        assert sym and " " not in sym, f"{v} 轉出的 Yahoo 代碼有問題：{sym!r}"


def test_datalist_covers_the_common_underlyings():
    html = (webapp.WEB_DIR / "index.html").read_text()
    for must in ("NVDA UW", "TSM UN", "AMD UW", "MSFT UW", "2330 TT", "700 HK"):
        assert f'value="{must}"' in html, f"建議清單缺少常用標的 {must}"


def test_ticker_inputs_are_wired_to_the_datalist():
    html = (webapp.WEB_DIR / "index.html").read_text()
    for uid in ("f-ud1", "f-ud2", "f-ud3", "f-ud4"):
        m = re.search(rf'<input id="{uid}"[^>]*>', html)
        assert m, f"找不到 {uid}"
        assert 'list="ticker-list"' in m.group(0), f"{uid} 未接上建議清單"


def test_coupon_field_advertises_both_modes():
    """配息欄位必須同時支援系統試算與手動輸入，且在介面上說清楚。"""
    html = (webapp.WEB_DIR / "index.html").read_text()
    m = re.search(r'<input id="f-coupon"[^>]*>', html, re.S)
    assert m, "找不到 f-coupon"
    tag = m.group(0)
    assert "系統試算" in tag
    assert "留白" in tag and "報價" in tag        # title 說明兩種模式
    assert 'id="btn-clear-coupon"' in html        # 一鍵切回系統試算
    assert "value=" not in tag                    # 預設留白 -> 預設由系統試算


def test_solve_state_labels_cover_every_blankable_field():
    """留白狀態提示的欄位名稱必須涵蓋所有可留白欄位。"""
    js = (webapp.WEB_DIR / "app.js").read_text()
    blankable = re.search(r"const BLANKABLE = \[(.*?)\];", js, re.S).group(1)
    ids = re.findall(r"'([\w-]+)'", blankable)

    labels = re.search(r"const FIELD_LABELS = \{(.*?)\};", js, re.S).group(1)
    labelled = set(re.findall(r"'([\w-]+)':", labels))

    assert set(ids) <= labelled, f"缺少留白欄位標籤：{sorted(set(ids) - labelled)}"


def test_client_side_errors_are_not_retried():
    """404 代表代碼不存在，重試只會讓使用者多等一輪退避才看到錯誤。"""
    import inspect

    from fcn import data

    src = inspect.getsource(data._http_get)
    assert "urllib.error.HTTPError" in src
    assert "exc.code != 429" in src           # 429 是流量限制，仍應重試
    assert "查無此標的" in src
