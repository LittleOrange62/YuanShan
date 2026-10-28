# -*- coding: utf-8 -*-
"""界面状态（`ys.ui_pref`）的判据回归。

用户 2026-09-28 的原话是「默认最近一次看过的票，不要总是变来变去」——
"变来变去"的来源正是**默认值**：以前趋势评分页缺省 000001，换浏览器/清缓存就回到它，
而在这种页面上"回到默认"等于悄悄换了一只票，页面不会告诉你。

这里只测判据（不连库）：没登记的键、形状不对的值都必须拒。
放行一个 `123` 进去，读回来就是"选中了一只不存在的票"，页面显示空表，
看着像那只票没数据 —— 排查的人会去查数据，不会去查这行状态。
"""
from __future__ import annotations

import pytest

from duanxian import ui_pref as up

pytestmark = pytest.mark.unit


def test_已登记的键就是这张单子():
    assert "scores.last_code" in up.PREFS
    keys = [r["key"] for r in up.registered()]
    assert keys == sorted(up.PREFS), "registered() 与 PREFS 漂了"


@pytest.mark.parametrize("bad", ["nope.key", "scores", ".x", "SCORES.last", "a.b.c", ""])
def test_没登记或形状不对的键一律拒(bad):
    with pytest.raises(up.PrefError):
        up._check(bad, "000001")


@pytest.mark.parametrize("bad", ["123", "abcdef", "600519X", "1234567"])
def test_代码值必须正好六位数字(bad):
    with pytest.raises(up.PrefError):
        up._check("scores.last_code", bad)


def test_空值表示没有选择是合法的():
    assert up._check("scores.last_code", "") == ""
    assert up._check("scores.last_code", "   ") == ""


@pytest.mark.parametrize("good", ["600519", "600519.SH", "920627.BJ", "000001.SZ"])
def test_分组自选手输那只收裸码也收带后缀的全长码(good):
    """明细行交回的是 `600519.SH`，人手工打的多半是 `600519` —— 两个入口要能写同一个键。"""
    assert up._check("grouped.dossier_code", good) == good


@pytest.mark.parametrize("bad", ["60051", "600519X", "600519.sh", "600519.XX", "abc123"])
def test_分组自选那个键不收半截码与小写后缀(bad):
    with pytest.raises(up.PrefError):
        up._check("grouped.dossier_code", bad)


def test_两个入口各用各的键():
    """`scores.last_code` 只收 6 位裸码，`grouped.dossier_code` 收全长码 —— 共用一键会互相悄悄换票。"""
    assert up.PREFS["scores.last_code"]["pattern"].pattern == r"^\d{6}$"
    assert up.PREFS["grouped.dossier_code"]["pattern"].pattern == r"^\d{6}(\.(SH|SZ|BJ))?$"


# 重点关注（持仓）的显示范围（用户 2026-10-14：「这里能选分组，不能选个股，你这个功能没做好」）。
# 放行一个 `code:60051` 进去，读回来就是"选中了一只不存在的票"、页面给一张空表，
# 看着像那只票没数据 —— 与文件开头那条是同一种坑。
@pytest.mark.parametrize("good", ["all", "watchlist", "group:7", "code:600519", "code:000001"])
def test_持仓范围认这四档形状(good):
    assert up._check("positions.scope", good) == good


@pytest.mark.parametrize("bad", ["nope", "CODE:600519", "code:60051", "code:6005199",
                                 "code:600519.SH", "code:abc123", "group:0", "group:",
                                 "code:600519extra"])
def test_持仓范围不收半截代码与册外的档(bad):
    with pytest.raises(up.PrefError):
        up._check("positions.scope", bad)


def test_持仓范围那支代码不带交易所后缀():
    """持仓表里的代码是 `600519.SH`，档位值统一取前 6 位（前端 `code6()`），
    两边各写一种形状的话，筛子就会永远筛不到 —— 所以这里只收裸 6 位。"""
    assert up._check("positions.scope", "code:600519") == "code:600519"
    with pytest.raises(up.PrefError):
        up._check("positions.scope", "code:600519.SH")


def test_合法值会被裁掉首尾空白():
    assert up._check("scores.last_code", "  002879 ") == "002879"


def test_拒绝的信息里要带上可用的键和要求的形状():
    """错误信息是页面上唯一能看见的东西 —— 只说"非法"等于让人去翻代码。"""
    with pytest.raises(up.PrefError) as exc:
        up._check("scores.last_code", "abc")
    msg = str(exc.value)
    assert "^\\d{6}$" in msg or "6" in msg
    with pytest.raises(up.PrefError) as exc2:
        up._check("nope.key", "1")
    assert "scores.last_code" in str(exc2.value), "要告诉他哪些键是登记过的"
