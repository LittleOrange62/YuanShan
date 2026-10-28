# -*- coding: utf-8 -*-
"""均线拐头筛选条件（`ma_turn`，2026-10-21 新增）——用户 2026-10-20 夜的原话拆成**一条参数化规则**：

  「就是XX日线拐头的，比如说参数60，那就是60日线拐头的，并且怎么定义拐头，也可以使用参数，
    比如说长期下行 or 长期上行？如果选了 X 日前长期下行，叠加近 X 日 横住了或者有所回升，
    回升率（降低率）低于（高于）Y%」

四个维度全是参数：均线周期 N（`n`）＋ 长期段（`long_days` 回看 M 日、方向 `long_dir`）
＋ 近期段（`recent_days` = X 日、形态 `recent_shape`）＋ 阈值（`threshold_pct` = Y，配 `cmp`）。

这个文件钉四件事（任务原编号）：
① 长期下行＋近期回升＋幅度够 ⇒ 命中；幅度不够 ⇒ 不命中（纯判据函数、假数据）；
② 斜率翻号但幅度不够不算拐头（只有末端翻号、近期没到 Y，不算）；
③ 非法参数出声并点名（n 出区间、Y 负数或 >50、方向值不认、X≥M、缺数），不许静默当作没筛；
④ 这条读的是后复权那份：源码断言 —— 登记的模板里出现的是 `close × adjustment_factor` 现算，
   **不碰** `analytics.indicator_value`（那张表只留 94 场因子行，长期段一超出就静默给空名单）。

后几条**不打桩**，直接读库里登记行（`app/db/sql/47_ma_turn_screen.sql` 已实跑）——
桩会把「库里到底登记成什么样」打没了（10-10 那次 18 小时假绿就是这个形状）。
"""
from __future__ import annotations

import os
import re

import pytest

from app.compute import ma_turn, screener

#: 一组纯判据用的假均线值：{距基准日的交易日偏移: 后复权 ma_N}
def _ma(a0=92.0, a1=91.5, ar=90.0, al=100.0, recent_days=10, long_days=60) -> dict:
    return {0: a0, 1: a1, recent_days: ar, long_days: al}

KW_DOWN_RECOVER = dict(long_days=60, recent_days=10, long_dir="down",
                       recent_shape="recover", threshold_pct=0.5, cmp="gte")


# ---------------------------------------------------------------- ① 判据本体（纯函数，无库）

def test_长期下行近期回升幅度够算命中而幅度不够不算():
    hit = ma_turn.evaluate(_ma(), **KW_DOWN_RECOVER)
    assert hit["long_ok"] and hit["turn_ok"] and hit["shape_ok"] and hit["threshold_ok"]
    assert hit["hit"] is True
    # 幅度不够：均线确实翻头了（a0>a1）、长期在下方（a0<al）、近期在回升（a0>ar），
    # 但近期变化只有 0.05%，够不上 Y=0.5 —— **只有斜率翻号不算拐头**
    weak = ma_turn.evaluate(_ma(ar=91.95), **KW_DOWN_RECOVER)
    assert weak["long_ok"] is True and weak["turn_ok"] is True and weak["shape_ok"] is True
    assert weak["threshold_ok"] is False
    assert weak["hit"] is False, "回升率低于阈值的翻头不是这条要筛的拐头"


def test_上行拐头向下是对称的另一件事两档都要能筛():
    # 长期上行 + 近期回落 + 幅度够（ma_turn 的 up/fall 档，镜像判据）
    up = ma_turn.evaluate(_ma(a0=108.0, a1=108.5, ar=110.0, al=100.0),
                          **dict(KW_DOWN_RECOVER, long_dir="up", recent_shape="fall"))
    assert up["hit"] is True, up


def test_横住档走幅度小于等于阈值且天然是lte():
    # flat：近期段净变化幅度 ≤ Y 就算横住；cmp 给 gte 也不许变成另一件事（flat 天然 lte）
    flat = ma_turn.evaluate(_ma(ar=91.95),
                            **dict(KW_DOWN_RECOVER, recent_shape="flat", cmp="gte"))
    assert flat["shape_ok"] is True and flat["threshold_ok"] is True and flat["hit"] is True
    # 横过头了（幅度 2.22% > 0.5）就不算横住
    not_flat = ma_turn.evaluate(_ma(ar=90.0), **dict(KW_DOWN_RECOVER, recent_shape="flat"))
    assert not_flat["shape_ok"] is False and not_flat["hit"] is False


# ---------------------------------------------------------------- ② 末端翻号是硬门

def test_斜率没翻号就什么都不算():
    # 长期下行、近期也在回升，但基准日相对前一日还在走低（差分同号）——那不是拐头
    no_turn = ma_turn.evaluate(_ma(a1=92.5), **KW_DOWN_RECOVER)
    assert no_turn["long_ok"] is True and no_turn["shape_ok"] is True
    assert no_turn["turn_ok"] is False and no_turn["hit"] is False


def test_长期方向与末端同向的回升不算拐头而是顺势():
    # a0 > al：长期段本身就是上行，long_dir=down 的净趋势门先关掉
    trend_not_turn = ma_turn.evaluate(_ma(al=80.0), **KW_DOWN_RECOVER)
    assert trend_not_turn["long_ok"] is False and trend_not_turn["hit"] is False


# ---------------------------------------------------------------- ③ 非法参数出声并点名

def test_纯判据层的坏参数逐一点名():
    with pytest.raises(ValueError, match="threshold_pct"):
        ma_turn.evaluate(_ma(), **dict(KW_DOWN_RECOVER, threshold_pct=-1))
    with pytest.raises(ValueError, match="threshold_pct"):
        ma_turn.evaluate(_ma(), **dict(KW_DOWN_RECOVER, threshold_pct=60))
    with pytest.raises(ValueError, match="long_dir"):
        ma_turn.evaluate(_ma(), **dict(KW_DOWN_RECOVER, long_dir="sideways"))
    with pytest.raises(ValueError, match="cmp"):
        ma_turn.evaluate(_ma(), **dict(KW_DOWN_RECOVER, cmp="eq"))
    with pytest.raises(ValueError, match="recent_days"):
        ma_turn.check_params({"long_days": 10, "recent_days": 20})


def test_缺数不许当0参与并点名缺哪一段():
    broken = dict(_ma())
    del broken[60]
    with pytest.raises(ValueError, match="长期段"):
        ma_turn.evaluate(broken, **KW_DOWN_RECOVER)


def test_引擎按schema把坏枚举坏区间点名退回():
    rule = screener.rules_map()["ma_turn"]
    props = screener.schema_props(rule)
    with pytest.raises(screener.ScreenError, match="cmp"):
        screener._coerce("cmp", props["cmp"], "eq")
    with pytest.raises(screener.ScreenError, match="threshold_pct"):
        screener._coerce("threshold_pct", props["threshold_pct"], "-1")
    with pytest.raises(screener.ScreenError, match="threshold_pct"):
        screener._coerce("threshold_pct", props["threshold_pct"], "51")
    with pytest.raises(screener.ScreenError, match="n"):
        screener._coerce("n", props["n"], "3")
    with pytest.raises(screener.ScreenError, match="n"):
        screener._coerce("n", props["n"], "abc")


def test_经引擎拼SQL时坏参数照实抛ScreenError而不是静默空名单():
    # 长期段比近期段还短：跨参数的关系，schema 单值校验表达不了，靠注册的校验器出声
    with pytest.raises(screener.ScreenError, match="long_days"):
        screener.build([{"rule_key": "ma_turn", "params": {"long_days": "10", "recent_days": "60"}}],
                       universe="watchlist")
    with pytest.raises(screener.ScreenError, match="recent_shape"):
        screener.build([{"rule_key": "ma_turn", "params": {"recent_shape": "updown"}}],
                       universe="watchlist")


# ---------------------------------------------------------------- 登记表在册（读真库，不打桩）

def test_ma_turn在册且七个参数都有默认与白名单():
    rule = screener.rules_map().get("ma_turn")
    assert rule, "ma_turn 没登记或已停用：这一屏就选不到它"
    schema = rule["param_schema"]
    if isinstance(schema, str):
        schema = __import__("json").loads(schema)
    props = schema["properties"]
    assert sorted(props) == sorted(["n", "long_days", "long_dir", "recent_days",
                                    "recent_shape", "threshold_pct", "cmp"])
    assert sorted(schema["required"]) == sorted(props), "七个参数全是必填（缺了由读层点名）"
    # 默认值只住后端这一处：界面从 /api/screen/rules 读，不许在组件里抄第二份
    assert props["n"]["default"] == 60
    assert props["long_days"]["default"] == 60
    assert props["recent_days"]["default"] == 10
    assert props["long_dir"]["default"] == "down" and props["recent_shape"]["default"] == "recover"
    assert props["threshold_pct"]["default"] == 0.5 and props["cmp"]["default"] == "gte"
    for name, allowed in (("long_dir", list(ma_turn.LONG_DIRS)),
                          ("recent_shape", list(ma_turn.RECENT_SHAPES)),
                          ("cmp", list(ma_turn.CMPS))):
        assert sorted(props[name]["enum"]) == sorted(allowed), f"{name} 的枚举与判据模块对不上"
        assert not props[name].get("literal"), f"{name} 在模板里是 CASE 的比较对象，走绑定不是文本替换"
    for name in ("n", "long_days", "recent_days", "threshold_pct"):
        lo, hi = ma_turn.BOUNDS[name]
        assert props[name]["minimum"] == lo and props[name]["maximum"] == hi, \
            f"{name} 的区间与判据模块的 BOUNDS 对不上"


def test_判据一句话随接口交出且界面不必自己写():
    """用户要求：界面上把那句判据原文显示出来（读接口，不自己写一句）。
    登记行的 `note` 就是那句话的宿主 —— 它随 `/api/screen/rules` 与每次结果的 `caliber` 交出去。"""
    from duanxian import screen_view as sv

    row = next(r for r in sv.rules_view()["rules"] if r["rule_key"] == "ma_turn")
    assert row["note"] and ma_turn.CRITERION in row["note"]
    out = sv.run_many([{"rule_key": "ma_turn", "params": {}}],
                      universe="watchlist", limit=5,
                      date=_latest_trading_day())
    assert out["available"] is True, out.get("reason")
    assert ma_turn.CRITERION in out["caliber"], "跑完的口径里也要带着判据原文"
    # 参数没给时按登记默认生效，并把实际生效值交回（默认只住后端一处的另一面）
    assert out["params"] == {"n": 60, "long_days": 60, "long_dir": "down", "recent_days": 10,
                             "recent_shape": "recover", "threshold_pct": 0.5, "cmp": "gte"}


def _latest_trading_day() -> str:
    from app.db import engine
    return str(engine.scalar("SELECT max(trade_date) FROM market_data.stock_daily",
                              role="reader"))[:10]


# ---------------------------------------------------------------- ④ 后复权那份（源码断言）

def test_这条的模板现算后复权均线而不是碰未复权那份():
    tpl = screener.rules_map()["ma_turn"]["sql_template"]
    # hfq 写法与 35_ma_hfq_screen.sql 一字一条：因子 >0 才用，否则按 1
    assert "x.close * CASE WHEN x.adjustment_factor > 0 THEN x.adjustment_factor ELSE 1 END" in tpl, \
        "均线必须用后复权收盘价现算 —— 未复权均线会在除息日把「下行」判成「走平」（133/4,933 只的教训）"
    # 不读 analytics.indicator_value：那张表只留最近 94 场因子行，long_days 超出就静默空名单
    assert "indicator_value" not in tpl, "读因子表会让长期段超出留存时静默给出空名单"
    # 模板里出现的价列只有现算 px 用到的 close × 因子，没有裸的第二把尺子
    assert "ma5" not in tpl and "ma60" not in tpl, "这条不吃固定档均线名 —— N 是现算的任意周期"
    assert "%" not in tpl, "模板里有 % —— psycopg 会当占位符起手"
    assert "::" not in tpl, "模板里有 :: —— 占位符正则会把后半截当参数吃掉"
    names = sorted(set(re.findall(r":([a-z_][a-z0-9_]*)", tpl)))
    assert names == sorted(["d", "n", "long_days", "long_dir", "recent_days",
                            "recent_shape", "threshold_pct", "cmp"]), "模板占位符与登记的参数对不上"


def test_迁移文件在册且判据与登记行同源():
    path = os.path.join(os.path.dirname(__file__), "..", "app", "db", "sql", "47_ma_turn_screen.sql")
    raw = open(path, encoding="utf-8").read()
    assert "ma_turn" in raw and "adjustment_factor" in raw
    assert "后复权" in raw, "脚本头部要记下为什么是后复权那份"
    # 四个取值点一个都不能少：t / t−1 / t−X / t−M
    for probe in ("rn = 0", "rn = 1", "rn = :recent_days", "rn = :long_days"):
        assert probe in raw, f"模板缺了取值点 {probe} —— 拐头四门少一门"


def test_这条不参与因子闸因为它根本不读因子表():
    """`screen_view` 那道 `_indicators_ready` 闸只拦模板里出现 indicator_value 的条件；
    ma_turn 现算，不该被闸住，也不该在闸关着时假装通过——用一条独立探针钉住两段语义。"""
    from duanxian import screen_view as sv

    assert sv.IND_TABLE not in screener.rules_map()["ma_turn"]["sql_template"]


# ---------------------------------------------------------------- 拼出来的 SQL 形状

def test_拼出的SQL带齐绑定参数且同一条出现两次不互相覆盖():
    conds = [{"rule_key": "ma_turn", "params": {"n": "20"}},
             {"rule_key": "ma_turn", "params": {"n": "120"}}]
    sql, params = screener.build(conds, universe="market", limit=50, logic="and")
    assert "%(c1_n)s" in sql and "%(c2_n)s" in sql, "两条同规则不同 N，参数前缀必须分开"
    assert params["c1_n"] == 20 and params["c2_n"] == 120
    assert params["c1_long_dir"] == "down", "枚举走绑定参数"
    assert "adjustment_factor" in sql
    # 一次真跑（自选池小母体，读库只读不写）：能出 SQL 能出数，不许静默炸
    rows = screener.run(conds, universe="watchlist", limit=5, role="reader")
    assert isinstance(rows, list)
