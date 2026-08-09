"""網頁層測試：請求解析、前端資產完整性，以及不依賴網路的錯誤路徑。"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from fcn import webapp
from fcn.terms import KIType, KOType

BASE = {
    "underlyings": ["AAA", "BBB"],
    "tenor_months": 12,
    "coupon_pa": "12",
    "strike_pct": "80",
    "ko_type": "Daily Memory",
    "autocall_pct": "100",
    "ki_type": "AKI",
    "ki_pct": "60",
    "rebate": "3",
}


def payload(**kw):
    return {**BASE, **kw}


# --------------------------------------------------------------------------
# 前端資產
# --------------------------------------------------------------------------


def test_web_assets_exist():
    for name in ("index.html", "app.js", "style.css"):
        assert (webapp.WEB_DIR / name).is_file(), f"缺少前端資產 {name}"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能檢查 JS 語法")
def test_app_js_parses():
    """語法錯誤會讓整個前端靜默失效，必須擋在測試層。"""
    r = subprocess.run(
        ["node", "--check", str(webapp.WEB_DIR / "app.js")],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_index_references_assets():
    html = (webapp.WEB_DIR / "index.html").read_text()
    assert "/static/style.css" in html
    assert "/static/app.js" in html


def test_every_form_id_used_by_app_js_exists_in_html():
    """app.js 以 id 取用表單欄位；漏一個就會在執行期才炸開。"""
    import re

    html = (webapp.WEB_DIR / "index.html").read_text()
    js = (webapp.WEB_DIR / "app.js").read_text()
    html_ids = set(re.findall(r'id="([\w-]+)"', html))
    # app.js 動態建立的欄位
    dynamic = {"f-ki", "f-lowercall"}
    used = set(re.findall(r"\$\('([\w-]+)'\)", js))
    missing = used - html_ids - dynamic
    assert not missing, f"app.js 取用了 HTML 沒有的 id：{sorted(missing)}"


# --------------------------------------------------------------------------
# 表單解析
# --------------------------------------------------------------------------


def test_parses_full_terms():
    t, blank = webapp.terms_from_payload(payload())
    assert blank is None
    assert t.underlyings == ["AAA", "BBB"]
    assert t.coupon_pa == pytest.approx(0.12)
    assert t.strike_pct == pytest.approx(0.80)
    assert t.ki_pct == pytest.approx(0.60)
    assert t.rebate == pytest.approx(0.03)
    assert t.ko_type is KOType.DAILY_MEMORY
    assert t.ki_type is KIType.AKI


@pytest.mark.parametrize(
    "field,key",
    [
        ("coupon_pa", "coupon_pa"),
        ("strike_pct", "strike_pct"),
        ("autocall_pct", "autocall_pct"),
        ("ki_pct", "ki_pct"),
        ("rebate", "rebate"),
    ],
)
def test_blank_field_is_detected(field, key):
    _, blank = webapp.terms_from_payload(payload(**{key: ""}))
    assert blank == field


def test_blank_lower_call_only_for_upside():
    _, blank = webapp.terms_from_payload(
        payload(upside=True, lower_call_strike_pct="", participation="100")
    )
    assert blank == "lower_call_strike_pct"

    # 非 Upside 產品不解析參與表現價
    t, blank2 = webapp.terms_from_payload(payload(lower_call_strike_pct="103"))
    assert blank2 is None
    assert t.lower_call_strike_pct is None


def test_two_blanks_are_rejected():
    with pytest.raises(webapp.ApiError, match="只能留白一個欄位"):
        webapp.terms_from_payload(payload(coupon_pa="", strike_pct=""))


def test_blank_ki_ignored_when_ki_type_is_na():
    t, blank = webapp.terms_from_payload(payload(ki_type="NA", ki_pct=""))
    assert blank is None
    assert t.ki_type is KIType.NONE
    assert t.ki_pct is None


def test_solving_ki_keeps_placeholder_below_strike():
    """求解下限價時的佔位值必須通過「下限價 < 執行價」的建構驗證。"""
    t, blank = webapp.terms_from_payload(payload(ki_pct="", strike_pct="55"))
    assert blank == "ki_pct"
    assert t.ki_pct < t.strike_pct


def test_solving_strike_keeps_placeholder_above_ki():
    t, blank = webapp.terms_from_payload(payload(strike_pct="", ki_pct="95"))
    assert blank == "strike_pct"
    assert t.strike_pct > t.ki_pct


def test_rejects_bad_underlying_count():
    with pytest.raises(webapp.ApiError, match="1~4 檔"):
        webapp.terms_from_payload(payload(underlyings=[]))
    with pytest.raises(webapp.ApiError, match="1~4 檔"):
        webapp.terms_from_payload(payload(underlyings=["A", "B", "C", "D", "E"]))


def test_rejects_unparsable_number():
    with pytest.raises(webapp.ApiError, match="不是有效數字"):
        webapp.terms_from_payload(payload(strike_pct="八十"))


def test_rejects_unknown_enum():
    with pytest.raises(webapp.ApiError, match="KI Type"):
        webapp.terms_from_payload(payload(ki_type="XKI"))
    with pytest.raises(webapp.ApiError, match="KO Type"):
        webapp.terms_from_payload(payload(ko_type="Hourly"))


def test_surfaces_term_validation_errors():
    with pytest.raises(webapp.ApiError, match="必須低於執行價"):
        webapp.terms_from_payload(payload(ki_pct="90", strike_pct="80"))


def test_path_requires_complete_terms():
    with pytest.raises(webapp.ApiError, match="請填入"):
        webapp.api_path(payload(coupon_pa="", trade_date="2024-06-03"))


def test_path_requires_trade_date():
    with pytest.raises(webapp.ApiError, match="請指定交易日"):
        webapp.api_path(payload())


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------


def test_terms_json_is_serialisable_and_flags_platform_limits():
    t, _ = webapp.terms_from_payload(payload(tenor_months=24, rebate="9"))
    j = webapp._terms_json(t)
    json.dumps(j, ensure_ascii=False)          # 不可拋出
    assert any("天期" in w for w in j["platform_warnings"])
    assert any("行銷通路費" in w for w in j["platform_warnings"])


def test_payoff_curve_shape():
    t, _ = webapp.terms_from_payload(payload())
    pf = webapp._payoff_curve(t)
    assert len(pf["x"]) == len(pf["ki_hit"]) == len(pf["no_ki"]) == len(pf["buy_hold"])

    # 未觸及生效者恆為配息上限；已觸及生效者在執行價以下呈線性下滑
    assert all(abs(v - pf["coupon_total"]) < 1e-12 for v in pf["no_ki"])
    below = [(x, y) for x, y in zip(pf["x"], pf["ki_hit"]) if x < t.strike_pct]
    assert below[0][1] < below[-1][1]
    above = [y for x, y in zip(pf["x"], pf["ki_hit"]) if x > t.strike_pct]
    assert all(abs(y - pf["coupon_total"]) < 1e-12 for y in above)


def test_payoff_curve_includes_upside_participation():
    t, _ = webapp.terms_from_payload(
        payload(upside=True, lower_call_strike_pct="103", strike_pct="85", ki_type="NA",
                ki_pct="")
    )
    pf = webapp._payoff_curve(t)
    top = max(pf["ki_hit"])
    assert top > pf["coupon_total"], "Upside FCN 的報酬應可突破配息上限"


# --------------------------------------------------------------------------
# 服務啟動
# --------------------------------------------------------------------------


def test_lan_address_is_resolvable():
    addr = webapp._lan_address()
    assert addr and addr.count(".") == 3


@pytest.mark.parametrize(
    "host,public",
    [("127.0.0.1", False), ("localhost", False), ("LOCALHOST", False),
     ("::1", False), (" 127.0.0.1 ", False),
     ("0.0.0.0", True), ("192.168.1.10", True), ("::", True)],
)
def test_public_bind_detection(host, public):
    assert webapp.is_public_bind(host) is public


@pytest.fixture
def fake_server(monkeypatch):
    class _Fake:
        def __init__(self, *a, **k): pass
        def serve_forever(self): raise KeyboardInterrupt
        def server_close(self): pass

    monkeypatch.setattr(webapp, "ThreadingHTTPServer", _Fake)


def test_public_bind_without_token_warns(monkeypatch, capsys, fake_server):
    """對外綁定又沒設通行碼時，必須明確警告服務是全開的。"""
    monkeypatch.delenv("FCN_TOKEN", raising=False)
    webapp.serve("0.0.0.0", 8000)
    out = capsys.readouterr().out
    assert "⚠" in out and "FCN_TOKEN" in out


def test_public_bind_with_token_reports_protection(monkeypatch, capsys, fake_server):
    monkeypatch.setenv("FCN_TOKEN", "s3cret")
    webapp.serve("0.0.0.0", 8000)
    out = capsys.readouterr().out
    assert "⚠" not in out and "通行碼" in out


def test_loopback_bind_is_quiet(monkeypatch, capsys, fake_server):
    monkeypatch.delenv("FCN_TOKEN", raising=False)
    webapp.serve("127.0.0.1", 8000)
    assert "⚠" not in capsys.readouterr().out


# --------------------------------------------------------------------------
# 圖表互動
# --------------------------------------------------------------------------


def test_crosshair_is_wired_into_the_shared_chart():
    """十字線查價由 lineChart 統一掛上，三張折線圖都應具備。"""
    js = (webapp.WEB_DIR / "app.js").read_text()
    assert "function attachCrosshair" in js
    assert "attachCrosshair(g, {" in js, "lineChart 未掛上十字線"
    assert "function nearestIndex" in js
    # 感應區必須能接到指標事件，且不被十字線圖層擋住
    assert "pointermove" in js and "pointerleave" in js
    assert "pointer-events:none" in js


def test_charts_supply_precise_hover_formats():
    """座標軸標籤取整即可，十字線查價必須給到小數位。"""
    js = (webapp.WEB_DIR / "app.js").read_text()
    assert js.count("hoverYFmt") >= 4      # 定義 + 三張圖各一
    assert js.count("hoverXFmt") >= 4
    assert "hoverFmt" in js                # 路徑圖以此同時顯示股價


# --------------------------------------------------------------------------
# 部署：PORT 解析與通行碼
# --------------------------------------------------------------------------


def test_resolve_bind_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    assert webapp.resolve_bind() == ("127.0.0.1", 8000)


def test_resolve_bind_follows_paas_port(monkeypatch):
    """PaaS 以 PORT 指派通訊埠，且服務必須對外綁定才會被路由到。"""
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.delenv("HOST", raising=False)
    assert webapp.resolve_bind() == ("0.0.0.0", 8080)


def test_resolve_bind_explicit_args_win(monkeypatch):
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setenv("HOST", "0.0.0.0")
    assert webapp.resolve_bind("127.0.0.1", 9999) == ("127.0.0.1", 9999)


def test_access_token_off_by_default(monkeypatch):
    monkeypatch.delenv("FCN_TOKEN", raising=False)
    assert webapp.access_token() is None
    monkeypatch.setenv("FCN_TOKEN", "   ")
    assert webapp.access_token() is None
    monkeypatch.setenv("FCN_TOKEN", " s3cret ")
    assert webapp.access_token() == "s3cret"


class _Req:
    """最小可用的 Handler 替身，只驗證通行碼判定。"""

    def __init__(self, cookie=None):
        self.headers = {"Cookie": cookie} if cookie else {}

    _authed = webapp.Handler._authed


def test_auth_open_when_no_token(monkeypatch):
    monkeypatch.delenv("FCN_TOKEN", raising=False)
    assert _Req()._authed() is True


@pytest.mark.parametrize(
    "cookie,ok",
    [
        (None, False),
        ("fcn_auth=wrong", False),
        ("fcn_auth=s3cret", True),
        ("other=x; fcn_auth=s3cret", True),
        ("fcn_auth=s3cre", False),
        ("fcn_auth=s3cretX", False),
    ],
)
def test_auth_checks_cookie(monkeypatch, cookie, ok):
    monkeypatch.setenv("FCN_TOKEN", "s3cret")
    assert _Req(cookie)._authed() is ok


def test_deployment_files_are_consistent():
    """Zeabur 依 zbpack.json 找進入點，進入點必須存在且會啟動服務。"""
    root = webapp.WEB_DIR.parent.parent
    cfg = json.loads((root / "zbpack.json").read_text())
    entry = cfg["python"]["entry"]
    assert (root / entry).is_file(), f"zbpack.json 指向不存在的進入點 {entry}"

    src = (root / entry).read_text()
    assert "serve()" in src and "from fcn.webapp import serve" in src

    reqs = (root / "requirements.txt").read_text()
    assert "numpy" in reqs and "pandas" in reqs
