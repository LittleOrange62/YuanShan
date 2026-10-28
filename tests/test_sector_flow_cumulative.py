"""`duanxian/sector_flow.py`（首页/板块热度页那两条「累计净流入」）的读侧口径回归。

这个模块原先**一条测试都没有** —— 于是那句带着裸 `%s` 的口径文案直接上了屏，
而"库里只有 1 场却被叫成近 3 个月累计"也没人兜住（用户 2026-10-08 当场判
"累计数肯定是错的"）。这里钉的是：文案里的占位符必须换成实数、分母（场数）必须
跟着数一起出去、以及**数本身不许算错**（只有 1 场时两列累计就该等于末场那一列）。

全程打桩 `pg.fetch_df`：不连库、不联网。
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

from duanxian import pg, sector_flow as sf

SOURCE = sf.SOURCE


def _fake_db(monkeypatch, *, sessions: int, lo: str, hi: str, rows: list[dict],
             caps: list[dict] | None = None, links: list[dict] | None = None,
             link_status: dict | None = None):
    """按 SQL 里出现的关键词分派：覆盖度、逐行业 CTE、市值那一维、东财板块对表。

    读不到的那一种（返回 None）也要能试出来 —— 打桩的意义就是让"库不通/没登记"
    这几条分支在不连库的情况下跑一遍。
    """
    def fake(sql, params=()):
        text = " ".join(str(sql).split())
        if "count(distinct trade_date)" in text:
            return pd.DataFrame([{"n": sessions, "lo": lo, "hi": hi}])
        if "em_board_link" in text:
            # status() 那句是聚合（group by），load() 那句是逐行名单
            return pd.DataFrame([] if link_status is None else [link_status]) \
                if "group by" in text else pd.DataFrame(links or [])
        if "stock_snapshot" in text and "sector_member" in text:
            return pd.DataFrame(caps or [])
        if "sector_fund_flow_daily" in text:
            return pd.DataFrame(rows)
        return None

    monkeypatch.setattr(pg, "fetch_df", fake)
    monkeypatch.setattr(pg, "enabled", lambda: True)


def _row(name="电力", net=27.82, days=1):
    """一行逐行业聚合。列集合就是 `aggregate()` 里那条 CTE 交出的那些 ——
    加一列要在这里补一列，否则打桩会把"新列读回来是空"演成"新列算错了"。"""
    return {"sector_name": name, "sum_since": net * days, "days_since": days,
            "sum_recent": net * days, "days_recent": days, "last_day": dt.date(2026, 10, 8),
            "net_last": net, "net_last_day": dt.date(2026, 10, 8), "pct_last": 1.23,
            "firms_last": 110,
            # 10-15 那一批新列（流入/流出拆开、为正天数、逐日 ln 复利）
            "inflow_recent": (net + 1.0) * days, "outflow_recent": 1.0 * days,
            "pos_days_recent": days,
            "ln_recent": days * math.log(1.05), "pct_days_recent": days}


def _cap(name="电力", float_yi=1000.0, total_yi=1500.0, priced=50, members=60):
    return {"name": name, "float_yi": float_yi, "total_yi": total_yi,
            "priced": priced, "members": members, "as_of": "2026-10-08"}


def test_口径里不许有没替换的占位符(monkeypatch):
    _fake_db(monkeypatch, sessions=1, lo="2026-10-08", hi="2026-10-08", rows=[_row()])
    d = sf.aggregate()
    for key, text in d["calibers"].items():
        assert "%s" not in text, f'口径 {key} 把占位符原样发上了屏'


def test_不满窗时场数与窗口都写进文案(monkeypatch):
    """用户读的是列名，不是下面的小字：分母（库里实际几场）必须出现在口径正文里。"""
    _fake_db(monkeypatch, sessions=1, lo="2026-10-08", hi="2026-10-08", rows=[_row()])
    d = sf.aggregate()
    three = d["calibers"]["sum_3m"]
    assert "1 场" in three and "2026-07-08" in three and "2026-10-08" in three
    assert "不等于" in three or "就等于末场" in three
    since = d["calibers"]["sum_since"]
    assert "1 场" in since and "2024-01-01" in since


def test_覆盖度交出窗口内的场数(monkeypatch):
    """`coverage.sessions_recent` 是"这个窗口最多几场"，页面判满没满窗就读它，不自己猜 60。"""
    _fake_db(monkeypatch, sessions=1, lo="2026-10-08", hi="2026-10-08", rows=[_row()])
    cov = sf.aggregate()["coverage"]
    assert cov["sessions"] == 1 and cov["sessions_recent"] == 1
    assert cov["from"] == "2026-10-08" and cov["to"] == "2026-10-08"


def test_零场时按读不到处理并说清原因(monkeypatch):
    """0 场不能交空 rows 了事：这张表 10-06 才建，过去没存过、回填不了 —— 这句话要在。"""
    _fake_db(monkeypatch, sessions=0, lo=None, hi=None, rows=[])
    d = sf.aggregate()
    assert d["available"] is False and d["rows"] == []
    assert "还没有任何一场" in d["reason"]
    for text in d["calibers"].values():
        assert "%s" not in text


def test_前端标题不再承诺二〇二四年以来():
    """列名与标题要跟着覆盖走：表 10-06 才建，"2024 年以来"这个名字在承诺一个不存在的窗口。

    钉源码是因为这一类"名字比数活得久"的措辞不会自己更新 —— 改成随 coverage 说话之后，
    谁把它改回写死的年份，这条就红。
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "frontend" / "src"
    cum = (root / "components" / "SectorFlowCumulative.tsx").read_text(encoding="utf-8")
    home = (root / "components" / "home" / "MarketPanorama.tsx").read_text(encoding="utf-8")
    heads = re.findall(r"<h2[^>]*>(.{0,120})", cum)
    assert not any("2024 年以来累计净流入（" in h for h in heads), heads
    assert "dash(d.coverage?.sessions)" in cum, "标题要把库里实际场数打出来"
    assert 'const FLOW_TABLE_LABEL = "覆盖以来累计净流入";' in home
    assert "sessions_recent?: number" in (root / "lib" / "api.ts").read_text(encoding="utf-8")


# ------------------------------------------------------------------ 10-15 那一批新列（加权 / 涨跌幅 / 流入流出拆开）

def test_加权强度用流通市值做分母_缺市值的那行是空不是零(monkeypatch):
    """用户点名的那一把尺子：同样一笔净流入，相对 1,000 亿和相对 0.37 万亿不是一件事。

    分母读不回来时必须交空 —— 交 0% 会被读成「一点也没灌进来」，那是把不知道卖成零。
    """
    _fake_db(monkeypatch, sessions=2, lo="2026-10-07", hi="2026-10-08",
             rows=[_row("电力", net=20.0, days=2), _row("传媒", net=20.0, days=2)],
             caps=[_cap("电力", float_yi=1000.0)])
    d = sf.aggregate()
    by = {r["sector_name"]: r for r in d["rows"]}
    assert by["电力"]["net_pct_float_recent"] == 4.0        # 40 亿 ÷ 1,000 亿 = 4%
    assert by["传媒"]["net_pct_float_recent"] is None
    assert by["传媒"]["float_mv_yi"] is None
    assert "mv_missing" in [c["code"] for c in d["caveats"]]
    assert d["market_value"]["covered"] == 1 and d["market_value"]["industries"] == 2


def test_窗口涨跌幅是逐日复利_缺涨跌幅的行不按零涨幅(monkeypatch):
    """`chg_recent_pct` 走 exp(Σln)−1，两天各 +5% 是 10.25% 不是 10%；一天都没有时交空。"""
    full = _row("电力", net=1.0, days=2)                   # ln_recent = 2·ln(1.05)
    none = _row("传媒", net=1.0, days=2)
    none.update({"ln_recent": None, "pct_days_recent": 0})
    _fake_db(monkeypatch, sessions=2, lo="2026-10-07", hi="2026-10-08", rows=[full, none])
    d = sf.aggregate()
    by = {r["sector_name"]: r for r in d["rows"]}
    assert by["电力"]["chg_recent_pct"] == 10.25
    assert by["电力"]["chg_days"] == 2
    assert by["传媒"]["chg_recent_pct"] is None, "缺涨跌幅不等于平盘"
    assert "chg_partial" in [c["code"] for c in d["caveats"]], "复利覆盖天数少于落库天数要出声"


def test_流入与流出各交绝对额_为正天数跟着分母走(monkeypatch):
    """「天天小灌」与「最后一天冲一笔」的累计可以相同 —— 这两列就是把它们分开的东西。"""
    row = _row("电力", net=10.0, days=4)
    _fake_db(monkeypatch, sessions=4, lo="2026-10-05", hi="2026-10-08", rows=[row])
    r = sf.aggregate()["rows"][0]
    assert r["inflow_sum_recent"] == 44.0 and r["outflow_sum_recent"] == 4.0
    assert r["net_avg_recent"] == 10.0
    assert r["pos_days_recent"] == 4 and r["days_recent"] == 4


# ------------------------------------------------------------------ 板块名 → 东财板块页（用户 2026-10-15 点名要能点开）

def test_对上了才给码_对不上就是空(monkeypatch):
    """读侧只认对表里真有的那一档（`sector_kind='行业'`），没码的行一律 None。

    这一条钉的是"不猜地址"：界面拿不到码就退回纯文本，所以后端**绝不能**把一个空
    对表读成"所有行业都对应同一个板块"。
    """
    _fake_db(monkeypatch, sessions=1, lo="2026-10-08", hi="2026-10-08",
             rows=[_row("油气开采"), _row("旅游及酒店")],
             links=[{"sector_name": "油气开采", "em_board_code": "BK1276",
                     "em_board_name": "油气开采Ⅱ", "match_kind": "roman_ii", "tried_at": "2026-10-15"}],
             link_status={"sector_kind": "行业", "linked": 73, "total": 90, "tried_at": "2026-10-15"})
    d = sf.aggregate()
    by = {r["sector_name"]: r for r in d["rows"]}
    assert by["油气开采"]["em_board_code"] == "BK1276"
    assert by["油气开采"]["em_board_name"] == "油气开采Ⅱ", "东财那一侧的真名要交出去，点开页眉是它"
    assert by["旅游及酒店"]["em_board_code"] is None
    assert d["board_link"]["rows_linked"] == 1 and d["board_link"]["rows_total"] == 2, \
        '这一榜自己的分母要在（90 行旁边只念全场那个数，会被读成这张表缺了一大片链接）'
    assert d["board_link"]["dict_linked"] == 73 and d["board_link"]["dict_total"] == 90
    assert d["board_link"]["tried_at"] == "2026-10-15", "对表新鲜度要跟着数一起出去"


def test_对表读不到时整列退回纯文本并说清原因(monkeypatch):
    """`em_board_link` 空表 / 没跑过 ⇒ 一个码都不给，并在 caveats 里写怎么手工刷一次。

    这一条是「检测器坏的时候不许装没事」的读侧版本：静默少掉一片链接，
    屏幕读起来和"今天就是没有链接"一模一样。"""
    _fake_db(monkeypatch, sessions=1, lo="2026-10-08", hi="2026-10-08", rows=[_row("油气开采")])
    d = sf.aggregate()
    assert d["rows"][0]["em_board_code"] is None
    assert d["board_link"]["available"] is False
    codes = [c["code"] for c in d["caveats"]]
    assert "board_link_off" in codes
    assert any("c24_em_board_link" in c["text"] for c in d["caveats"])
