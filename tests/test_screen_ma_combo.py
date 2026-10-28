# -*- coding: utf-8 -*-
"""筛选器的**多条件组合**与均线族条件（2026-10-14 补齐）。

用户那两句是这一组测试的存在理由：
  「250线以下，其他均线密集的这些个股」
  「最好能兼容其他的，比如说年线以下，月线比10日线高，等等各种条件可以多个组合筛选」

当时缺两段：登记表里 5 条条件没有一条读均线；而执行引擎 `screener.build(conds, logic=…)`
一直是收**条件数组**的（参数名前缀 `c{i}_` 就是为"同一条条件带不同参数出现两次"准备的），
但读层只传单条、页面只能选一条 —— 所以这一族测试钉的是"数组这条路真的走得通"，
而不只是"多了一条条件"。

另有一条不是顺手加的闸：均线类条件读 `analytics.indicator_value`，而**那一场的因子还没算时
`EXISTS` 会一只都不给**，页面于是写"这条条件一只都没命中" —— 那是取数没跑到，
不是筛出来空集，两者在屏幕上长得一样（同一条教训写在 `app/compute/pattern.py` 头部）。
"""
from __future__ import annotations

import pytest

from app.compute import screener
from duanxian import screen_view as sv

MA = {
    "ma_position": {
        "rule_key": "ma_position", "name": "价在指定均线的上方／下方", "enabled": True,
        "builtin": True, "note": "复权收盘价与该条复权均线的偏离度（%）",
        "sql_template": "EXISTS (SELECT 1 FROM analytics.indicator_value i "
                        "WHERE i.security_code = d.security_code AND i.trade_date = :d "
                        "AND i.indicator_key = ':ma' AND (d.close * CASE WHEN "
                        "d.adjustment_factor > 0 THEN d.adjustment_factor ELSE 1 END - i.value) "
                        "/ i.value * 100 :op :pct)",
        "param_schema": {"type": "object", "required": ["ma", "op"],
                         "properties": {
                             "ma": {"enum": ["ma20", "ma250"], "literal": True, "default": "ma250"},
                             "op": {"enum": [">", "<"], "literal": True, "default": "<"},
                             "pct": {"type": "number", "default": 0}}},
    },
    "dense_short5": {
        "rule_key": "dense_short5", "name": "短五线密集", "enabled": True, "builtin": True,
        "note": "五条短周期复权均线的极差 ÷ 最低那根",
        "sql_template": "EXISTS (SELECT 1 FROM analytics.indicator_value i "
                        "WHERE i.security_code = d.security_code AND i.trade_date = :d "
                        "AND i.indicator_key IN ('ma5','ma10','ma20','ma30','ma60') "
                        "GROUP BY i.security_code HAVING count(*) = 5 "
                        "AND (max(i.value) - min(i.value)) / nullif(min(i.value), 0) * 100 <= :max_pct)",
        "param_schema": {"type": "object", "required": ["max_pct"],
                         "properties": {"max_pct": {"type": "number", "default": 3}}},
    },
    "limit_up": {
        "rule_key": "limit_up", "name": "N 日内涨停过", "enabled": True, "builtin": True,
        "note": None,
        "sql_template": "EXISTS (SELECT 1 FROM market_data.stock_daily x WHERE x.trade_date = :d)",
        "param_schema": {"type": "object", "required": [],
                         "properties": {"days": {"type": "integer", "default": 5}}},
    },
    # 一条"必填且没登记默认值"的，专门用来验报缺参时说没说清是第几条
    "needs_thing": {
        "rule_key": "needs_thing", "name": "没给值就不跑的那种条件", "enabled": True,
        "builtin": False, "note": None,
        "sql_template": "EXISTS (SELECT 1 FROM market_data.stock_daily x WHERE x.trade_date = :d)",
        "param_schema": {"type": "object", "required": ["value"],
                         "properties": {"value": {"type": "number"}}},
    },
}


@pytest.fixture()
def stub(monkeypatch):
    """打桩登记表 / 执行 / 母体计数 / 因子行探测 —— 判据要在没库的时候也能测。"""
    from app.compute import screener as sc

    monkeypatch.setattr(sc, "rules_map", lambda **kw: {k: dict(v) for k, v in MA.items()})
    calls: dict = {}

    def fake_run(conds, **kw):
        calls["conds"] = conds
        calls.update(kw)
        return [{"代码": "600000.SH", "名称": "浦发银行"}]

    monkeypatch.setattr(sc, "run", fake_run)
    monkeypatch.setattr(sv.engine, "query_one", lambda *a, **k: {"n": 1200})
    monkeypatch.setattr(sv.engine, "scalar", lambda *a, **k: __import__("datetime").date(2026, 10, 14))
    state = {"ind_rows": 5562}
    monkeypatch.setattr(sv, "_indicators_ready",
                        lambda target: state["ind_rows"] > 0 if state["ind_rows"] is not None else None)
    return {"calls": calls, "state": state}


# ---------------------------------------------------------------- 组合这件事本身

def test_两条条件一起交且顺序就是页面上的顺序(stub):
    out = sv.run_many([{"rule_key": "ma_position", "params": {"ma": "ma250", "op": "<"}},
                       {"rule_key": "dense_short5", "params": {"max_pct": "2"}}])
    assert out["available"] is True, out.get("reason")
    assert [c["rule_key"] for c in stub["calls"]["conds"]] == ["ma_position", "dense_short5"]
    # 每条交自己实际生效的参数，不是一份合并后的字典 —— 四条条件各有 pct，合成一份就读不出谁的
    assert out["conditions"][0]["params"]["ma"] == "ma250"
    assert out["conditions"][1]["params"]["max_pct"] == "2"
    assert [c["index"] for c in out["conditions"]] == [1, 2]


def test_多条时logic原样交给引擎且默认是并且(stub):
    sv.run_many([{"rule_key": "limit_up", "params": {}}, {"rule_key": "limit_up", "params": {}}])
    assert stub["calls"]["logic"] == "and"
    stub["calls"].clear()
    sv.run_many([{"rule_key": "limit_up", "params": {}}, {"rule_key": "limit_up", "params": {}}],
                logic="or")
    assert stub["calls"]["logic"] == "or", "或者 / 并且是两套完全不同的名单，不能吃掉这个开关"


def test_关系只认and和or(stub):
    out = sv.run_many([{"rule_key": "limit_up", "params": {}}], logic="AND NOT")
    assert out["available"] is False and "and / or" in out["reason"]


def test_一条都没有时不替你跑全候选集(stub):
    """空条件在执行引擎里等于 `where true`（把整个候选集交出来）。
    那不是一个筛选结果，页面上会读成"全部命中"。"""
    out = sv.run_many([])
    assert out["available"] is False and "一条条件都没有" in out["reason"]
    assert "conds" not in stub["calls"]


def test_叠到第七条就出声而不是悄悄丢掉后几条(stub):
    conds = [{"rule_key": "limit_up", "params": {}}] * (sv.MAX_CONDS + 1)
    out = sv.run_many(conds)
    assert out["available"] is False and str(sv.MAX_CONDS) in out["reason"]


def test_缺参数时报是第几条缺哪一项(stub):
    """`max_pct` 那种登记了默认值的不算缺；只有既 required 又没默认值才算。
    报的时候必须带第几条 —— 四条里第二条缺参却只说「这条缺」，页面上没人知道动哪一格。"""
    out = sv.run_many([{"rule_key": "limit_up", "params": {}},
                       {"rule_key": "needs_thing", "params": {}}])
    assert out["available"] is False
    assert "第 2 条" in out["reason"] and "value" in out["reason"], out["reason"]
    assert "conds" not in stub["calls"]


# ---------------------------------------------------------------- 均线族的两条纪律

def test_那一场没有因子行时不许塌成空命中(stub):
    """均线条件读 indicator_value。因子没算到那一天时 EXISTS 一只都不给，
    那必须报"取数没跑到"，不能报"筛出来空集"。"""
    stub["state"]["ind_rows"] = 0
    out = sv.run_many([{"rule_key": "ma_position", "params": {"ma": "ma250", "op": "<"}},
                       {"rule_key": "dense_short5", "params": {"max_pct": "2"}}])
    assert out["available"] is False
    assert "不是这几条条件一只都没命中" in out["reason"]
    assert "conds" not in stub["calls"], "判不了就不发那条会给出空名单的 SQL"


def test_不读因子的条件不受这道闸影响(stub):
    stub["state"]["ind_rows"] = 0
    out = sv.run_many([{"rule_key": "limit_up", "params": {}}])
    assert out["available"] is True, out.get("reason")


def test_探测本身失败时不替库下结论(stub, monkeypatch):
    """`_indicators_ready` 探不到交 None —— 那一刻"这一场没因子"是**没验过**，
    不是"确定没有"，所以照原样跑，但要在口径里说明没验过。"""
    monkeypatch.setattr(sv, "_indicators_ready", lambda target: None)
    out = sv.run_many([{"rule_key": "ma_position", "params": {}}])
    assert out["available"] is True
    assert "没能确认" in out["caliber"]


def test_口径里逐条把自己生效的参数与原话端出来(stub):
    out = sv.run_many([{"rule_key": "ma_position", "params": {"ma": "ma250", "op": "<", "pct": "0"}},
                       {"rule_key": "dense_short5", "params": {"max_pct": "2"}}], logic="or")
    c = out["caliber"]
    assert "1. 价在指定均线的上方／下方" in c and "2. 短五线密集" in c
    assert "ma=ma250" in c and "max_pct=2" in c
    assert "或者" in c, "条件之间怎么合，必须跟名单一起交出去"


# ---------------------------------------------------------------- 登记表本身的三条形状
# 这几条**不打桩**，直接读库里的登记行：桩会把"库里到底登记成什么样"这件事打没了
# （10-10 那次 18 小时假绿就是这个形状 —— 两边各按各的假设写，一起错、一起绿）。

def test_登记表里均线族四条条件在册且都在白名单内():
    rmap = screener.rules_map()
    for key, params in (("ma_position", {"ma": "ma", "op": "op", "pct": "pct"}),
                        ("ma_pair", {"ma_a": "ma_a", "ma_b": "ma_b", "op": "op"}),
                        ("dense_short5", {"max_pct": "max_pct"}),
                        ("dense_mid4", {"max_pct": "max_pct"}),
                        ("align_bull", {"min_layers": "min_layers"}),
                        ("align_bear", {"min_layers": "min_layers"})):
        assert key in rmap, f"{key} 没登记或已停用：这一屏就选不到它"
        props = screener.schema_props(rmap[key])
        for name in params:
            assert name in props, f"{key} 缺参数 {name}"
        for name in ("ma", "ma_a", "ma_b", "op", "dir"):
            spec = props.get(name)
            if spec:
                assert spec.get("literal") is True and spec.get("enum"), \
                    f"{key}.{name} 是文本替换进 SQL 的，没有 enum 白名单等于开了注入面"


def test_模板里价与均线同底且没有百分号与双冒号():
    for key in ("ma_position", "ma_pair", "align_bull", "align_bear"):
        tpl = screener.rules_map()[key]["sql_template"]
        assert "%" not in tpl, f"{key} 的模板里有 % —— psycopg 会把它当占位符起手，整条 SQL 报错"
        assert "::" not in tpl, f"{key} 的模板里有 :: —— 占位符正则会把 ::text 的后半截当参数吃掉"
    # 价那一侧必须乘因子（与 config.indicator 里那条均线同底），裸 d.close 就是两套口径
    for key in ("ma_position", "align_bull", "align_bear"):
        tpl = screener.rules_map()[key]["sql_template"]
        assert "adjustment_factor" in tpl, f"{key} 拿不复权价去比复权均线"


def test_密集条件要求组内每条均线都在():
    """缺一条均线的票如果被当成"极差很小"，会被排到最前面 —— 那是把缺数读成结论。"""
    for key, n in (("dense_short5", 5), ("dense_mid4", 4)):
        tpl = screener.rules_map()[key]["sql_template"]
        assert f"count(*) = {n}" in tpl, f"{key} 没有 `HAVING count(*) = {n}` 这道闸"
