"""全市场换手率榜（09-27 由成交额榜改过来）的四条回归。

打桩在线取数与 PG，不连库不联网。守的是四件会静默出错的事：
1. **排序键必须在取数那一侧定**（`fid="f8"`）。拿回成交额榜再在前端按换手率重排，
   得到的是"成交额前 20 里的换手率榜"——不报错、看着也像一张榜。
2. 过滤/排序都按 `turnover`，不是按 `amount`。
3. 没有概念行的票交 `None`（界面显示 `—`），**不补"无题材"**；缺口数量要交出去。
4. 换手率拿不到（源回 '-' 或整轮失败）⇒ 空榜 + reason，不拿成交额榜顶位。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# vr **追加**而不是插到 0：仓库根有 `app/` 包、vr 里有 `app.py`，插到 0 会让
# 后面任何 `from app.compute import …` 拿到 vr/app.py（一个模块）而报"不是包"。
if str(ROOT / "vr") not in sys.path:
    sys.path.append(str(ROOT / "vr"))


ROWS = [
    {"code": "300301", "name": "低换手", "price": 10.0, "pct": 1.0,
     "amount": 9e9, "turnover": 3.2, "mcap": 1e10, "float_cap": 9e9, "industry": "半导体"},
    {"code": "301689", "name": "电科思仪", "price": 70.0, "pct": 20.0,
     "amount": 2.5e9, "turnover": 56.7, "mcap": 7.7e10, "float_cap": 6e10, "industry": "通用设备"},
    {"code": "920201", "name": "N百瑞吉", "price": 72.0, "pct": 331.0,
     "amount": 6e8, "turnover": 97.3, "mcap": 4.9e9, "float_cap": 5.5e8, "industry": "医疗器械"},
    {"code": "000002", "name": "缺换手", "price": 8.0, "pct": -1.0,
     "amount": 5e8, "turnover": None, "mcap": 8e9, "float_cap": 7e9, "industry": "房地产"},
]


@pytest.fixture
def market(monkeypatch):
    import astock
    import vr.market as market
    captured: dict = {}

    def fake_rank(n: int = 20, fid: str = "f6"):
        captured["n"], captured["fid"] = n, fid
        return [dict(r) for r in ROWS]

    monkeypatch.setattr(astock, "market_turnover_rank", fake_rank)
    from duanxian import pg_sources
    from duanxian import trade_calendar
    # 时段与行情日钉死：否则同一份代码在夜里/周末跑出来的红绿不一样
    monkeypatch.setattr(trade_calendar, "quote_trade_day", lambda: "2026-09-24")
    monkeypatch.setattr(trade_calendar, "session_phase",
                        lambda now, day: {"phase_key": "open", "phase": "盘中",
                                          "label": "盘中 · 连续交易", "poll": True})
    monkeypatch.setattr(pg_sources, "concepts_by_code",
                        lambda codes: {"301689": {"concepts": "太赫兹、6G概念", "concept_count": 15},
                                       "300301": {"concepts": None, "concept_count": None}})
    # 别拿上一轮的缓存糊过这一轮的断言。2026-10-26 起这一层的缓存收成共享那一份
    # （`duanxian/ttlcache.py`，键带 `market:` 前缀），原来那句 `market._CACHE.clear()`
    # 清的是已被删掉的那第二套表 —— 按前缀清才对，**这条测试的意图一个字没放松**。
    from duanxian import ttlcache
    ttlcache.clear('market:')
    return market, captured


def test_rank_is_requested_by_turnover_not_by_amount(market):
    m, captured = market
    out = m.get_turnover_top()
    assert captured["fid"] == "f8", "排序键交给取数侧：fid 必须是 f8（换手率），不是 f6（成交额）"
    to = [r["turnover"] for r in out["stocks"]]
    assert to == sorted(to, reverse=True), "榜单顺序必须按换手率降序"
    assert [r["code"] for r in out["stocks"]] == ["920201", "301689", "300301"], \
        "换手率最高在前；成交额 90 亿那只不能因为额大就排前面"
    assert out["rank_basis"] == "turnover"


def test_missing_concept_rows_stay_null_and_are_counted(market):
    m, _ = market
    out = m.get_turnover_top()
    by = {r["code"]: r for r in out["stocks"]}
    assert by["301689"]["concepts"] == "太赫兹、6G概念" and by["301689"]["concept_count"] == 15
    assert by["300301"]["concepts"] is None, "库里没有概念行 = None（界面 `—`），不许编成「无题材」"
    assert by["920201"]["concepts"] is None
    assert out["concept_missing"] == 2, "两只没有概念行要如实交出去，页面才说得出「—」是什么意思"
    assert "不等于它不属于任何题材" in out["concept_note"]


def test_no_turnover_data_yields_empty_board_with_reason(market, monkeypatch):
    m, _ = market
    import astock
    monkeypatch.setattr(astock, "market_turnover_rank",
                        lambda n=20, fid="f6": [dict(r, turnover=None) for r in ROWS])
    from duanxian import ttlcache
    ttlcache.clear('market:')       # 同上：清共享那一份的 `market:` 那一族键
    out = m.get_turnover_top()
    assert out["stocks"] == [] and "换手率" in out["reason"], \
        "换手率取不到只能给空榜 + 原因；退回成交额榜就是拿另一张榜冒充"


def test_upstream_helper_defaults_to_amount_ranking():
    """默认值仍是 f6（成交额）：改动只对这一张榜生效，别的地方调用不能跟着变序。"""
    import inspect
    import astock
    sig = inspect.signature(astock.market_turnover_rank)
    assert sig.parameters["fid"].default == "f6"
    src = inspect.getsource(astock.market_turnover_rank)
    assert "f8" in src, "返回里必须带换手率字段，否则这张榜没有可排的数"
