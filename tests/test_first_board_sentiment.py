# -*- coding: utf-8 -*-
"""「首板日大盘情绪」：这一轮最早那个封板日那天，大盘是什么档。

用户 2026-10-10 要求在短线情绪那块加这一列。这里钉住四件容易写错的事：

1. **读的是首板日，不是今天**：日子取「几天几板」那条链交回的 `board_dates[0]`（升序里最早的一天），
   拿今天的档位去填这一格是最省事也最错的写法；
2. **主读数用 `market_sentiment_daily`（1,255 场不缺）**，`market_mood` 只作注记 —— 它从
   2026-06-29 起才有、最近 30 场实测缺 5 天，当主读数就会整列时不时空掉；
3. **缺数是空，不是 0**：mood 那把尺子上 0 的意思是「中性」，把"没有这个分"写成 0 会被读成一句结论；
4. **pandas 的 NaN 不许进 JSON**：LEFT JOIN 没命中的格子 pandas 读成 NaN，`json.dumps` 会原样产出
   非法字面量，浏览器 `JSON.parse` 直接炸 —— 整数列同样会被升成 float，所以也要收。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VR = ROOT / "vr"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# ⚠️ vr 只能**追加到末尾**，不能插到 0：仓库根有 `app/` 这个包，vr 里有个 `app.py`，
# 谁在前 `import app` 就认谁。duanxian.quant 顶层要 `from app.compute import startup_volume`，
# vr 排在前头时它拿到的是 vr/app.py（一个模块，不是包）⇒ 整串 import 直接炸
# （2026-10-26 单跑本文件实测 6 条红就红在这，与断言无关）。
if str(VR) not in sys.path:
    sys.path.append(str(VR))

import firstboard          # noqa: E402  market.py 里是函数内导入，patch 模块本身才盖得住
import market              # noqa: E402
from duanxian import board_span as bs
from duanxian import pg_sources as ps


_FULL = {'breadth_level': '偏强', 'breadth_score': 1.0, 'theme_level': '活跃', 'theme_score': 0.5,
         'limit_up_real': 62, 'up_count': 3200, 'down_count': 1800, 'ratio': 1.78,
         'source': 'legu', 'mood': 31.2}


# ------------------------------------------------------------------ 纯函数

def test_首板日没数出来时整格交空而不是拿今天顶():
    got = ps.first_board_view(None, None)
    assert got['first_board_day'] is None and got['first_board_breadth'] is None
    assert got['first_board_note'] == ''
    assert got['first_board_mood'] is None, "没这一天 ⇒ 不是 0 分、也不是今天那个分"


def test_档位读数逐项带到行上且悬停把输入说全():
    got = ps.first_board_view('2026-09-30', _FULL)
    assert (got['first_board_day'], got['first_board_breadth']) == ('2026-09-30', '偏强')
    assert got['first_board_theme'] == '活跃' and got['first_board_breadth_score'] == 1.0
    note = got['first_board_note']
    # 后缀写错（读 breadth 而不是 breadth_level）不会报错，只会让悬停永远显示「缺」——所以按值断言
    assert '偏强(+1.0)' in note and '活跃(+0.5)' in note, note
    assert '首板日 2026-09-30' in note and '涨停 62 家' in note and '涨/跌 3200/1800' in note
    assert '大盘情绪分 +31.2' in note


def test_那天没有mood这个分就说没有而不填零():
    sp = dict(_FULL, mood=None)
    got = ps.first_board_view('2026-09-16', sp)
    assert got['first_board_mood'] is None
    tail = got['first_board_note'].split('大盘情绪分')[1]
    assert '那天没有这个分' in tail and '+0.0' not in tail


def test_中性那一档的分值不带正号():
    sp = dict(_FULL, breadth_level='中性', breadth_score=0.0)
    assert '宽度 中性(0.0)' in ps.first_board_view('2026-09-30', sp)['first_board_note']


def test_那一天在档位表里没行时要说明是没行():
    got = ps.first_board_view('2026-09-30', None)
    assert got['first_board_day'] == '2026-09-30'
    assert got['first_board_breadth'] is None and got['first_board_mood'] is None
    note = got['first_board_note']
    assert '没有行' in note
    # 整行没有时只说这一件事：把「涨停 — 家」「宽度 缺」一起堆上去会被读成"那天什么都没涨"
    assert '涨停' not in note and '宽度' not in note, note


# ------------------------------------------------------------------ 出口清洗

def test_查询坏了要回报failed而不是交回一个空表(monkeypatch):
    monkeypatch.setattr(ps.pg, "fetch_df", lambda sql, params: None)
    assert ps.sentiment_on_dates(['2026-09-30']) == {'failed': True}
    assert ps.sentiment_on_dates([]) == {}, "没要日子就不该打库"


def test_整数列的NaN也收成None(monkeypatch):
    """LEFT JOIN 没命中时 pandas 会把 int 列升成 float（值变 NaN），NaN 进 JSON 就是非法字面量。"""
    import pandas as pd

    monkeypatch.setattr(ps.pg, "fetch_df", lambda sql, params: pd.DataFrame(
        [{'day': '2026-09-16', 'breadth_level': '偏强', 'breadth_score': 1.0,
          'theme_level': '活跃', 'theme_score': 0.5, 'limit_up_real': float('nan'),
          'up_count': float('nan'), 'down_count': float('nan'), 'ratio': float('nan'),
          'source': 'legu', 'mood': float('nan')}]))
    got = ps.sentiment_on_dates(['2026-09-16'])['2026-09-16']
    for k in ('limit_up_real', 'up_count', 'down_count', 'ratio', 'mood'):
        assert got[k] is None, k
    assert got['breadth_level'] == '偏强'
    json.dumps(got, allow_nan=False)      # 有 NaN 就抛；这一格是整张表要过 JSON 出口的


def test_文字列的NaN同样要收掉_这一天真在库上崩过(monkeypatch):
    """10-10 实测：查 2026-10-07（不是场次）时 breadth_level 那一格是 **float NaN 而不是 None**
    —— 一列里既有字符串又有 None 时 pandas 用 StringDtype，缺的那格填的是它的 na_value。
    只清洗数值列不够，文字列同样会把非法 JSON 带出去（浏览器 JSON.parse 直接炸）。"""
    import pandas as pd

    monkeypatch.setattr(ps.pg, "fetch_df", lambda sql, params: pd.DataFrame(
        [{'day': '2026-10-08', 'breadth_level': '偏弱', 'breadth_score': -1.0,
          'theme_level': '普通', 'theme_score': -0.5, 'limit_up_real': 39,
          'up_count': 1634, 'down_count': 3466, 'ratio': 0.47, 'source': 'legu', 'mood': -60.2},
         {'day': '2026-10-07', 'breadth_level': None, 'breadth_score': None,
          'theme_level': None, 'theme_score': None, 'limit_up_real': None,
          'up_count': None, 'down_count': None, 'ratio': None, 'source': None, 'mood': None}]))
    df = ps.pg.fetch_df('', ())
    assert str(pd.isna(df['breadth_level'].iloc[1])) == 'True', "这条测试的前提是 pandas 真的给 NaN"
    got = ps.sentiment_on_dates(['2026-10-08', '2026-10-07'])
    json.dumps(got, ensure_ascii=False, allow_nan=False)
    assert got['2026-10-07']['breadth_level'] is None and got['2026-10-07']['source'] is None
    assert got['2026-10-08']['breadth_level'] == '偏弱'
    assert '没有行' in ps.first_board_view('2026-10-07', got['2026-10-07'])['first_board_note']


# ------------------------------------------------------------------ 接法
#
# 2026-10-26：母体从"现抓东财池"改成读库，桩位随之从 `astock.em_zt_topic_pool`
# 换到 `duanxian.limit_up_board.board_on`（形状由 `tests/support_limit_up_board.py` 保证）。
# 下面六条断言一条没改：只查最早那天、整批一次查询、整批读不到要说读不到、
# 没有行要点名日子、封板日没数出来不报成缺行、口径文字随榜单交出且不带星号。

import support_limit_up_board as sup
from duanxian import limit_up_board


@pytest.fixture
def board(monkeypatch):
    def _apply(rows, boards, sent, sent_calls):
        monkeypatch.setattr(limit_up_board, "board_on",
                            lambda day: sup.fake_board(rows, day=day))
        monkeypatch.setattr(firstboard, "data_session", lambda: ("2026-10-08", None))
        monkeypatch.setattr(bs, "boards_by_code", lambda codes, day: boards)

        def fake_sent(dates):
            sent_calls.append(list(dates))
            return dict(sent)
        monkeypatch.setattr(ps, "sentiment_on_dates", fake_sent)
    return _apply


def _row(code, name, lbc):
    return sup.landed_row(code, name, lbc, seal=92500, order=1e7, cap=1e9, reopen=0)


def _b(dates):
    return {"days": len(dates), "boards": len(dates), "label": "%d天%d板" % (len(dates), len(dates)),
            "sealed_dates": dates, "rows": 30, "sealed_today": True, "has_today_row": True}


def test_读的是这一轮最早那天而不是今天(board):
    calls = []
    board([_row("000001", "甲", 3)],
         {"000001": _b(["2026-10-06", "2026-10-07", "2026-10-08"])},
         {"2026-10-06": dict(_FULL, breadth_level='冰点', breadth_score=-2.0, mood=None)}, calls)
    r = market._emotion()["lianban_stocks"][0]
    assert r["first_board_day"] == "2026-10-06", "首板日 = 那串封板日里最早的一天"
    assert r["first_board_breadth"] == "冰点"
    assert calls == [["2026-10-06"]], "只为首板日那一天打库，不拿今天的日子去凑"


def test_两只票同一个首板日只打一次库(board):
    calls = []
    board([_row("000001", "甲", 2), _row("000002", "乙", 2)],
         {"000001": _b(["2026-10-07", "2026-10-08"]), "000002": _b(["2026-10-07", "2026-10-08"])},
         {"2026-10-07": _FULL}, calls)
    out = market._emotion()
    assert len(calls) == 1, "整批一次查询（去重在取数那层），不是每行打一遍库"
    assert {r["first_board_breadth"] for r in out["lianban_stocks"]} == {"偏强"}


def test_整批读不到时说是这一列没读到(board):
    calls = []
    board([_row("000003", "丙", 2)], {"000003": _b(["2026-10-07"])}, {"failed": True}, calls)
    out = market._emotion()
    assert "没读到" in out["first_board_warn"], out["first_board_warn"]
    assert "不是这批票的首板日都没情绪" in out["first_board_warn"]
    assert out["lianban_stocks"][0]["first_board_breadth"] is None
    assert out["lianban_stocks"][0]["first_board_day"] == "2026-10-07", "日子仍交出：读者能看出是链路坏了不是没日子"


def test_那一天没有行时点名是哪个日子(board):
    calls = []
    board([_row("000004", "丁", 2)], {"000004": _b(["2026-10-07"])}, {}, calls)
    out = market._emotion()
    assert "2026-10-07" in out["first_board_warn"] and "没有行" in out["first_board_warn"]


def test_封板日没数出来时首板日为空且不报成缺行(board):
    calls = []
    board([_row("000005", "戊", 2)],
         {"000005": {"days": None, "boards": None, "label": None, "sealed_dates": [],
                     "rows": 30, "sealed_today": False, "has_today_row": False}},
         {}, calls)
    out = market._emotion()
    r = out["lianban_stocks"][0]
    assert r["first_board_day"] is None and r["first_board_note"] == ''
    assert out["first_board_no_span"] == 1
    assert out["first_board_warn"] == '', "空着的原因在「几天几板」那一列，这里不重复报"
    assert calls == [], "没日子可查就不该打库"


def test_口径文字随榜单一起交出且上界面那段没有星号(board):
    calls = []
    board([_row("000006", "己", 2)], {"000006": _b(["2026-10-07"])}, {"2026-10-07": _FULL}, calls)
    out = market._emotion()
    assert out["first_board_rule"] == ps.FIRST_BOARD_RULE_NOTE
    assert "**" not in out["first_board_rule"], "这段是页面上的悬停文字，不许带 markdown 粗体标记"
    assert "market_sentiment_daily" in out["first_board_rule"]
    assert "不填 0" in out["first_board_rule"]


# ------------------------------------------------------------- 取数开销（真库，不打桩）

def test_那一句mood只扫要的那几天而不是全史():
    """2026-10-26 首页排查实测：`market_mood` 在 `analytics.score` 里是**每场每票一行**，
    原来那句 `select distinct ... where capability_key='market_mood'` 不带日期条件，
    把全史百万行级扫完再去重（首页那一块单这一句 4.35 秒，限定到那几天 0.3 秒），
    而下游 left join 只按要查的那几天取 ⇒ 其余全扫一遍再丢掉。

    去掉那句日期条件**不会报错、结果也照旧**，只会让首页每天慢四秒 —— 所以这里钉两件事：
    ① SQL 里真的有那句日期条件；② 与「一天一天单独查」的独立算法逐字段对得上（不是拿自己比自己）。
    """
    from duanxian import pg

    src = (ROOT / "duanxian" / "pg_sources.py").read_text(encoding="utf-8")
    assert "and trade_date in (select td from d)" in src, \
        "mood 那一叠又不限日期了 —— 那正是首页单句 4.4 秒的成因"

    dates = pg.fetch_df(
        "select trade_date::text d from analytics.market_sentiment_daily "
        "where trade_date >= (select max(trade_date) from analytics.market_sentiment_daily) - 20 "
        "order by 1 desc limit 4")
    if dates is None:
        pytest.skip("库不通：这一条要真库读数才能对账")
    want = [str(x)[:10] for x in dates["d"].tolist()]
    assert want, "档位表读不到任何一场，无从对账"

    got = ps.sentiment_on_dates(want)
    assert got.get("failed") is not True, f"整批没读到：{got}"

    for d in want:
        one = pg.fetch_df(
            "select breadth_level, breadth_score::float bs, theme_level, theme_score::float ts, "
            "limit_up_real::int lu, up_count::int up, down_count::int dn, ratio::float rt, source "
            "from analytics.market_sentiment_daily where trade_date = %s::date", (d,))
        # 同场全市场同一个值 ⇒ distinct 应当至多一行；真出现两行就是那条口径假设变了，让这里出声
        mood = pg.fetch_df(
            "select distinct value::float v from analytics.score "
            "where capability_key = 'market_mood' and trade_date = %s::date", (d,))
        assert one is not None and mood is not None, f"{d} 独立复核打库失败"
        assert len(mood) <= 1, f"{d} 这一场 market_mood 有 {len(mood)} 个不同值，同场同值的假设不成立了"
        r = got[d]
        if len(one) == 0:
            assert r["breadth_level"] is None and r["mood"] is None, f"{d} 档位表没行，函数却交出了数"
            continue
        e = one.iloc[0]
        assert (r["breadth_level"], r["theme_level"], r["source"]) == (
            e["breadth_level"], e["theme_level"], e["source"]), f"{d} 档位/来源与独立查的不一样"
        assert r["breadth_score"] == e["bs"] and r["theme_score"] == e["ts"], f"{d} 分值对不上"
        assert (r["limit_up_real"], r["up_count"], r["down_count"]) == (
            e["lu"], e["up"], e["dn"]), f"{d} 家数对不上"
        assert r["mood"] == (float(mood.iloc[0]["v"]) if len(mood) else None), f"{d} 那个注记分对不上"
