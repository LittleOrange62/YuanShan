"""全库聚合缓存的三条纪律：算一次、认年龄、给重算的口子。

`/api/quant/quadrant` 慢的那 3~4 秒几乎全在"与看哪一天无关"的全表扫描上（象限规则实证、
空值占比、各表覆盖起点），所以它们进进程缓存。缓存换来两件事必须同时做到，否则宁可不做：
旧值要自带年龄（`cached_aggregates` caveat），并且要有 `refresh=True` 能立刻重算。
这里不打库，只验这三条行为。
"""
from __future__ import annotations

import time

import pytest

from duanxian import quant


@pytest.fixture(autouse=True)
def _clean_cache():
    quant._AGG.clear()
    yield
    quant._AGG.clear()


class Counter:
    def __init__(self, rows=None, one=None):
        self.calls = []
        self._rows = rows if rows is not None else []
        self._one = one if one is not None else {"n": 1, "tot": 2}

    def __call__(self, sql, params=()):
        self.calls.append(" ".join(sql.split())[:60])
        return self._rows

    def one(self, sql, params=()):
        self.calls.append(" ".join(sql.split())[:60])
        return self._one


def test_agg_computes_once_then_serves_cache(monkeypatch):
    counter = Counter(one={"tot": 100, "n": 25})
    monkeypatch.setattr(quant, "_one", counter.one)
    first = quant._agg("unit:ratio", "select count(*) tot, count(*) filter (where x is null) n from t")
    second = quant._agg("unit:ratio", "select count(*) tot, count(*) filter (where x is null) n from t")
    assert first == second == {"tot": 100, "n": 25}
    assert len(counter.calls) == 1, "第二次不该再打库"


def test_cache_caveat_only_names_values_reused_from_an_earlier_request(monkeypatch):
    quant._AGG["unit:ratio"] = (time.monotonic(), {"tot": 1, "n": 0})
    # 本次请求里刚算的（年龄≈0）不该被说成"缓存"，那是对着新鲜数喊旧
    assert quant._cache_caveat(("unit:ratio",)) == []
    quant._AGG["unit:ratio"] = (time.monotonic() - 40, {"tot": 1, "n": 0})
    (caveat,) = quant._cache_caveat(("unit:ratio",))
    assert caveat["code"] == "cached_aggregates"
    assert "unit:ratio" in caveat["text"] and "40" in caveat["text"]


def test_clear_agg_cache_is_scoped(monkeypatch):
    now = time.monotonic()
    quant._AGG.update({"coverage:score": (now, {}), "coverage:score_trend": (now, {}),
                       "quadrant_rule": (now, {}), "sector_heat:legacy": (now, {})})
    assert quant.clear_agg_cache("coverage:") == 2
    assert set(quant._AGG) == {"quadrant_rule", "sector_heat:legacy"}
    assert quant.clear_agg_cache() == 2


def test_unreachable_database_is_not_cached_as_none(monkeypatch):
    monkeypatch.setattr(quant, "_one", lambda sql, params=(): None)
    assert quant._agg("unit:down", "select 1") is None
    assert "unit:down" not in quant._AGG, "连不上库的那次不该被钉在缓存里一小时"


def test_refresh_flag_clears_this_endpoints_aggregates(monkeypatch):
    """refresh=True 只该清自己的三项，别把别的接口的缓存一锅端。"""
    spy = []
    monkeypatch.setattr(quant, "clear_agg_cache", lambda prefix="": spy.append(prefix))
    monkeypatch.setattr(quant, "_dash", lambda d: d)
    monkeypatch.setattr(quant, "_latest", lambda t: "2026-09-23")
    # 让 quadrant() 在清完缓存后立刻以"不可用"返回：session_has 挡在门口，
    # pg.enabled=False 让 _fail 走"未配置"那条分支 —— 否则它会去探针真库，
    # 全量跑时别的测试已经把网络闸刀压下来了（单跑绿、全量红就是这种）。
    monkeypatch.setattr(quant.pg, "enabled", lambda: False)
    monkeypatch.setattr(quant.pg, "session_has", lambda table, date: False)

    out = quant.quadrant("2026-09-23")
    assert spy == [] and out["available"] is False      # 默认不刷新
    out = quant.quadrant("2026-09-23", refresh=True)
    assert out["available"] is False
    assert spy == ["quadrant", "entry_quadrant_null_share", "coverage:"]


def test_null_coverage_reports_both_denominators(monkeypatch):
    answers = {
        "= 'oper_score'": {"tot": 275520, "n": 75995},
        "analytics.score_trend": {"tot": 314331, "n": 161344},
    }

    def fake_one(sql, params=()):
        tail = sql.rstrip()
        for needle, value in answers.items():
            if tail.endswith(needle):
                return value
        raise AssertionError(f"没预料到的聚合：{tail[:80]}")

    monkeypatch.setattr(quant, "_one", fake_one)
    out = quant._null_coverage("2026-09-23", 0)
    assert out["score_quadrant_null_pct_all_sessions"] == 27.6
    assert out["score_trend_first_quadrant_null_pct"] == 51.3
    # 只给一个数会被读错：本场为 0 与历史 27.6% 必须同时在
    assert out["score_selected_session"]["quadrant_null"] == 0
    assert out["score_quadrant_null_all_sessions"] == 75995
    assert "四格相加" in out["why"]


def test_ratio_never_divides_by_zero():
    assert quant._ratio(0, 0) is None
    assert quant._ratio(None, 10) is None
    assert quant._ratio(3, 12) == 25.0
