# -*- coding: utf-8 -*-
"""筛选器读层 `duanxian/screen_view.py`（`GET /api/screen`）。

这一层的存在理由是 2026-10-06 用户那一句「这个页面是干嘛的？具体怎么用？
没有应用筛选的页面呀？」—— 条件登记在 `config.screen_rule`、执行引擎在
`app/compute/screener.py`，但**没有任何 HTTP 入口调用它们**，所以那一屏只有清单、跑不动。

守的四条：
1. 缺 required 且没登记默认值 → 报**缺哪一项**，不猜一个值跑一遍（猜出来的名单会被当成用户筛的）；
2. 字面类参数（列名 / 比较符）只能取 `enum` 白名单 —— 这条门在执行引擎那边，读层不放宽也不自己拼 SQL；
3. 母体数与截断必须随行交出：自选池只有几十只时"筛出 8 只"与全市场"筛出 8 只"是两件事；
4. 停用的条件选不到，但清单里要看得见（隐藏会让人以为条件从来只有那些）。
"""
from __future__ import annotations

import pytest

from duanxian import screen_view as sv

RULES = {
    "limit_up": {
        "rule_key": "limit_up", "name": "N 日内涨停过", "enabled": True, "builtin": True,
        "note": "窗口内任一日收盘涨幅 ≥ pct",
        "param_schema": {"type": "object", "required": ["days"],
                         "properties": {"days": {"type": "integer", "default": 5},
                                        "pct": {"type": "number", "default": 9.9}}},
    },
    "price_compare": {
        "rule_key": "price_compare", "name": "指定日价格比较", "enabled": True, "builtin": True,
        "note": None,
        "param_schema": {"type": "object", "required": ["on_date", "field", "op", "value"],
                         "properties": {"op": {"enum": [">", "<"], "literal": True},
                                        "field": {"enum": ["open", "close"], "literal": True},
                                        "on_date": {"type": "string", "format": "date"},
                                        "value": {"type": "number"}}},
    },
}


@pytest.fixture()
def stub(monkeypatch):
    """打桩 rules_map / run / 母体计数 —— 这一层的判据要在没库的时候也能测。"""
    from app.compute import screener

    monkeypatch.setattr(screener, "rules_map", lambda **kw: dict(RULES))
    calls: dict = {}

    def fake_run(conds, **kw):
        calls["conds"] = conds
        calls.update(kw)
        return [{"代码": "600000.SH", "名称": "浦发银行", "收盘": 9.0, "涨跌幅": 1.2,
                 "换手率": 3.4, "操作分": 61.0, "象限": "Q2"}]

    monkeypatch.setattr(screener, "run", fake_run)
    monkeypatch.setattr(sv.engine, "query_one", lambda *a, **k: {"n": 42})
    monkeypatch.setattr(sv.engine, "scalar", lambda *a, **k: "2026-09-30")
    return calls


def test_缺必填且没默认值时报缺哪一项(stub, monkeypatch):
    from app.compute import screener
    out = sv.run("price_compare", {}, universe="watchlist")
    assert out["available"] is False
    for name in ("on_date", "field", "op", "value"):
        assert name in out["reason"], out["reason"]
    assert screener.run.__name__ and "conds" not in stub, "缺参数时一条 SQL 都不该发出去"


def test_字面参数过不了枚举就照实把原话端出来(stub, monkeypatch):
    from app.compute import screener

    def boom(conds, **kw):
        raise screener.ScreenError("参数 op 只能是 ['>', '<'] 之一，收到 '&gt;'")

    monkeypatch.setattr(screener, "run", boom)
    out = sv.run("price_compare", {"on_date": "2026-09-30", "field": "close",
                                   "op": "&gt;", "value": "10"}, universe="watchlist")
    assert out["available"] is False
    assert "只能是" in out["reason"]
    assert out["params"]["op"] == "&gt;", "把收到的原值交回去，人才知道是哪一格填错了"


def test_没给的条件参数按登记默认补且实际生效值随行交出(stub):
    out = sv.run("limit_up", {}, universe="watchlist")
    assert out["available"] is True, out.get("reason")
    assert out["params"] == {"days": 5, "pct": 9.9}, out["params"]
    assert stub["conds"] == [{"rule_key": "limit_up", "params": {"days": 5, "pct": 9.9}}]


def test_母体数与截断随行交出(stub):
    out = sv.run("limit_up", {"days": "3"}, universe="watchlist", limit=1)
    assert out["universe_count"] == 42
    assert out["universe_means"].startswith("自选池")
    assert out["returned"] == 1 and out["truncated"] is True, "达到上限必须说这只是前 N 行"
    assert out["columns"] == ["代码", "名称", "收盘", "涨跌幅", "换手率", "操作分", "象限"]


def test_候选集只认两种写法(stub):
    out = sv.run("limit_up", {}, universe="group:3")
    assert out["available"] is False and "watchlist / market" in out["reason"]


def test_不在册的条件把能用的列出来(stub):
    out = sv.run("nope", {})
    assert out["available"] is False
    assert "limit_up" in out["reason"] and "price_compare" in out["reason"]
    assert "启用中" in out["reason"], "停用的条件不参与筛选，要说清是这个状态而不是「没有这个概念」"


def test_异常不塌成空表(stub, monkeypatch):
    from app.compute import screener

    def boom(conds, **kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(screener, "run", boom)
    out = sv.run("limit_up", {})
    assert out["available"] is False and "RuntimeError" in out["reason"]
    assert out["rows"] == []


def test_参数表是机读结构而不是repr字符串(stub, monkeypatch):
    """`/api/screen/rules` 旧写法（经 pandas）把 jsonb 交成了 repr 字符串，
    界面只能给人看一眼、没法生成控件 —— 页面长不出"跑一次"就有一半卡在这里。"""
    from app.compute import screener
    monkeypatch.setattr(sv.engine, "query", lambda *a, **k: [
        {"rule_key": "limit_up", "name": "N 日内涨停过", "note": None, "builtin": True,
         "enabled": True, "has_sql": True, "param_schema": RULES["limit_up"]["param_schema"]}])
    view = sv.rules_view()
    p = view["rules"][0]["params"]
    assert isinstance(p, list) and p[0]["name"] == "days"
    assert [x["name"] for x in p] == ["days", "pct"], "必填排前面，页面按这个顺序摆控件"
    assert p[0]["required"] is True and p[0]["default"] == 5
    assert view["universes"][0]["key"] == "watchlist"


def test_上限取页面给的档位但不超登记表里那道硬门(stub):
    out = sv.run("limit_up", {}, limit=99999)
    assert out["limit"] == max(sv.LIMIT_CHOICES), "页面档位之外还有读层这道夹，防一次拉全表"
