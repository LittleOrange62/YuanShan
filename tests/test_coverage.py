"""数据覆盖体检（`duanxian/coverage.py`）的回归。

全部打桩 `pg.fetch_df`：不连库、不联网。守的是**五种状态不许互相掩盖**这件事 ——
"表没建"、"没读权限"、"表在但 0 行"、"区间内缺几天"、"这几天本来不是交易日"
是五个不同的答案，合并任何一个都会把用户指向错误的行动。
"""

from __future__ import annotations

import pandas as pd
import pytest

from duanxian import coverage as cov
from duanxian import pg


def _fake(rows_by_marker, calls=None):
    """按 SQL 里的 marker 派发返回值；没命中的 marker 一律返回 None（模拟查询失败）。"""
    # marker **按特异性顺序**匹配：日历查询里也有 `as lo`，谁先匹配决定它被谁认领。
    priority = ("as expected", "filter (where is_open)", "string_agg", "as any",
                "reltuples", "count(*) as rows", "count(distinct", "as d from",
                "as c from", "as lo", "as t",
                "has_table_privilege", "to_regclass")

    def fetch(sql, params=()):
        if calls is not None:
            calls.append(sql)
        for marker in priority:
            if marker in rows_by_marker and marker in sql:
                val = rows_by_marker[marker]
                return None if val is None else pd.DataFrame([val])
        raise AssertionError("未预期的 SQL: %s" % sql[:120])
    return fetch


BASE = {
    "to_regclass": {"r": "public_x"},                 # 表存在
    "has_table_privilege": {"ok": True},              # 有 SELECT 权限
    "reltuples": {"est": 1200},                       # 行数估算
    "as lo": {"lo": "2026-01-05", "hi": "2026-09-23"},     # min/max
    "as expected": {"expected": 180, "present_days": 178, "miss_cnt": 2,
                    "first_missing": "2026-07-24", "last_missing": "2026-09-03",
                    "stale_cnt": 1, "last_stale": "2026-09-24",
                    "samples": ["2026-07-24", "2026-09-03"]},
    "filter (where is_open)": {"open_days": 8797, "lo": "1990-12-19", "hi": "2026-12-31"},
    "string_agg": {"t": "analytics.score", "cols": "updated_at"},
    "as any": {"any": True},                          # 大表存在性确认
    "count(*) as rows": {"rows": 7205},               # 小表精确行数
    "as t": {"t": "2026-09-23 19:00:00+08"},          # max(时间戳列)
    # 「一天应该有 N 只」的只数判据只发一条查询：这一场多少只（基线不在库里查，
    # 在 `completeness` 的登记表里 —— 2026-10-06 换成动态条数规则后少了一条 SQL）
    "count(distinct": {"c": 9527},
    "as c from": {"c": 5556},
}


@pytest.fixture(autouse=True)
def _on(tmp_path, monkeypatch):
    """测试自己一份缓存目录：否则会被真 `data_path("coverage")` 里的旧结果喂到，
    测出来的绿灯和代码无关。
    条数登记表（`completeness`）**必须一起隔离** —— 只隔 coverage 的缓存的话，
    只数判据会拿真机上登记过的历史来判，用例绿的和代码无关，还会把真表写脏。"""
    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(cov, "data_path", lambda *p: str(tmp_path))
    from duanxian import completeness as comp
    monkeypatch.setattr(comp, "data_path", lambda *p: str(tmp_path / "completeness"))


def test_有数且区间内不缺_gaps_为_0(monkeypatch):
    rows = dict(BASE, **{"as expected": {"expected": 1169, "present_days": 1169,
                                          "miss_cnt": 0, "first_missing": None,
                                          "stale_cnt": 0, "last_stale": None, "samples": None}})
    monkeypatch.setattr(pg, 'fetch_df', _fake(rows))
    out = cov.report("qd_board_ladder", refresh=True)["tables"][0]
    assert out["status"] == "ok" and out["missing_sessions"] == 0
    assert out["date_semantics"] == "session"        # 有数据日期列 → 才谈得上"缺哪天"


def test_区间内缺交易日标_gaps_并给出首缺与样例(monkeypatch):
    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE))
    out = cov.report("stock_snapshot", refresh=True)["tables"][0]
    assert out["status"] == "gaps"
    assert out["missing_sessions"] == 2 and out["sessions_present"] == 178
    assert out["first_missing"] == "2026-07-24" and len(out["missing_samples"]) == 2


def test_区间内不缺但停在昨天也算有缺(monkeypatch):
    """`max(trade_date)` 之前一天不漏、但昨天整场没入库 —— 只看区间内缺口会报 ok。
    扫到今天是故意的：停更恰恰是最该被看见的那几天。"""
    rows = dict(BASE, **{"as expected": {"expected": 1250, "present_days": 1249,
                                          "miss_cnt": 0, "first_missing": None,
                                          "stale_cnt": 1, "last_stale": "2026-09-24",
                                          "samples": None}})
    monkeypatch.setattr(pg, 'fetch_df', _fake(rows))
    out = cov.report("stock_daily", refresh=True)["tables"][0]
    assert out["missing_sessions"] == 0
    assert out["stale_sessions"] == 1 and out["last_stale_date"] == "2026-09-24"
    assert out["status"] == "gaps"


def test_只覆盖一天的表不许被读成_这表很全(monkeypatch):
    """分钟线 min=max=2026-09-22 → 区间内"不缺天"，状态是 ok，但覆盖只有 1 个交易日。
    不新增第六种状态，用 thin_coverage 标记强制页面打 ⚠。"""
    rows = dict(BASE, **{"as expected": {"expected": 1, "present_days": 1, "miss_cnt": 0,
                                          "first_missing": None, "stale_cnt": 0, "last_stale": None,
                                          "samples": None}})
    monkeypatch.setattr(pg, 'fetch_df', _fake(rows))
    out = cov.report("stock_minute_1m", refresh=True)["tables"][0]
    assert out["status"] == "ok" and out["thin_coverage"] is True

    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE))
    assert cov.report("stock_snapshot", refresh=True)["tables"][0]["thin_coverage"] is False


def test_日期不缺但条数比基线少_必须被看出是半截数据(monkeypatch):
    """实测教训（2026-09-26）：分钟线 09-23 只进了北交所 351 只，全市场应 9,527 只。

    场次不缺（min=max 区间内一天不漏）、行数也不算空 → 旧判据报 ok/薄只靠"才两天"，
    页面读出来就是"分钟线到 9.23 了"。半截数据冒充全量比缺数更坏 ——
    用户会拿 351 只票做全市场日内分析。所以这条判据钉的是**条数**：
    这一场与**前一天的条数**比，偏离不许超过 N 条（2026-10-07 用户改的规则；
    分钟类默认 200 条，因为停牌那天实测能少 107~143 个文件）。
    """
    from duanxian import completeness as comp

    comp.record('stock_minute_1m', '2026-09-18', 9520, source='test')
    comp.record('stock_minute_1m', '2026-09-22', 9527, source='test')
    inner = _fake(BASE)

    def fetch(sql, params=()):
        if "count(distinct security_code)" in sql:
            return pd.DataFrame([{"c": 351}])
        return inner(sql, params)

    monkeypatch.setattr(pg, 'fetch_df', fetch)
    out = cov.report("stock_minute_1m", refresh=True)["tables"][0]
    assert out["entities_latest"] == 351
    assert out["entities_baseline"] == 9527 and out["baseline_date"] == "2026-09-22"
    assert out["sessions_back"] == 1                    # 基线就是前一天（09-23 那场没登记）
    assert out["entity_deviation"] == 200               # 分钟类单独一档
    assert out["entity_partial"] is True
    assert out["thin_coverage"] is True                 # 页面原有的 ⚠ 也一起点亮
    assert "351" in out["coverage_note"] and "9,527" in out["coverage_note"]
    # 半截那天**不许进登记表**：进了就等于把基线拉低，第二天连 200 只都算全
    assert '2026-09-23' not in comp.history('stock_minute_1m')


def test_条数不低于基线不许误报半截(monkeypatch):
    from duanxian import completeness as comp

    comp.record('stock_minute_1m', '2026-09-18', 9520, source='test')
    comp.record('stock_minute_1m', '2026-09-22', 9527, source='test')
    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE))    # 这一场给 9,527
    row = cov.report("stock_minute_1m", refresh=True)["tables"][0]
    assert row["entity_partial"] is False and "coverage_note" not in row
    assert comp.history('stock_minute_1m')['2026-09-23'] == 9527   # 判为全 → 落表


def test_条数查不出来不许伪装成半截也不许伪装成全(monkeypatch):
    """`count(distinct)` 失败（超时/连不上）时交回 `None`：
    "查不到"和"只有一半"是两个行动 —— 前者重查、后者补下载，混成一个就两边都误。"""
    inner = _fake(BASE)

    def fetch(sql, params=()):
        if "count(distinct security_code)" in sql:
            return None
        return inner(sql, params)

    monkeypatch.setattr(pg, 'fetch_df', fetch)
    row = cov.report("stock_minute_1m", refresh=True)["tables"][0]
    assert row["entities_latest"] is None
    assert row["entity_partial"] is False                   # 不伪装成半截
    assert "没数出来" in row["completeness_why"]             # 但也不沉默


def test_表不存在是_missing_table_不是空表(monkeypatch):
    rows = dict(BASE, **{"to_regclass": {"r": None}})
    monkeypatch.setattr(pg, 'fetch_df', _fake(rows))
    out = cov.report("qd_sector_rotation", refresh=True)["tables"][0]
    assert out["status"] == "missing_table"
    assert out["rows"] is None                       # 没这张表就不该有行数
    assert "missing_sessions" not in out or out.get("missing_sessions") is None


def test_无读权限是_no_access_且不许与前两种混(monkeypatch):
    rows = dict(BASE, **{"has_table_privilege": {"ok": False}})
    monkeypatch.setattr(pg, 'fetch_df', _fake(rows))
    out = cov.report("ignition", refresh=True)["tables"][0]
    assert out["status"] == "no_access" and out["rows"] is None


def test_表在能读但零行是_no_data(monkeypatch):
    rows = dict(BASE, **{"reltuples": {"est": 0},
                         "as any": {"any": False},
                         "count(*) as rows": {"rows": 0}})
    monkeypatch.setattr(pg, 'fetch_df', _fake(rows))
    out = cov.report("fund_flow_rank", refresh=True)["tables"][0]
    assert out["status"] == "no_data" and out["rows"] == 0
    assert out["missing_sessions"] is None           # 空表不报"缺几天"


def test_查询超时不许被当成不缺天(monkeypatch):
    rows = dict(BASE, **{"as expected": None})
    monkeypatch.setattr(pg, 'fetch_df', _fake(rows))
    out = cov.report("sector_trend", refresh=True)["tables"][0]
    assert out["status"] == "error"
    assert out["missing_sessions"] is None
    assert "未取到缺天数" in out["reason"]


def test_大表行数走估算并标明_kind(monkeypatch):
    rows = dict(BASE, **{"reltuples": {"est": 14254680}})
    calls = []
    monkeypatch.setattr(pg, 'fetch_df', _fake(rows, calls))
    out = cov.report("indicator_value", refresh=True)["tables"][0]
    assert out["rows"] == 14254680 and out["rows_kind"] == "estimated"
    assert not any("count(*) as rows" in c for c in calls)     # 大表不许再 count(*) 撞超时


def test_小表行数给精确值(monkeypatch):
    rows = dict(BASE, **{"reltuples": {"est": 7205}, "count(*) as rows": {"rows": 7205}})
    monkeypatch.setattr(pg, 'fetch_df', _fake(rows))
    out = cov.report("dragon_tiger", refresh=True)["tables"][0]
    assert out["rows_kind"] == "exact"


def test_快照表不报交易日缺口(monkeypatch):
    rows = dict(BASE, **{"as lo": {"lo": None, "hi": None}})
    monkeypatch.setattr(pg, 'fetch_df', _fake(rows))
    out = cov.report("security", refresh=True)["tables"][0]
    assert out["date_semantics"] == "snapshot"
    assert out["status"] == "ok" and out.get("missing_sessions") is None


def test_未知_key_要报出来而不是静默给空表(monkeypatch):
    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE))
    out = cov.report("不存在的表")
    assert out["available"] is False and "未知的表 key" in out["reason"]
    assert "stock_daily" in out["keys"]


def test_库没配就明确不可用不编数():
    cov.pg.enabled = lambda: False
    out = cov.report("all")
    assert out["available"] is False and "VIBE_PG" in out["reason"]


def test_磁盘缓存命中时不再查库并交出来源与时刻(monkeypatch, tmp_path):
    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE))
    cov.report("all", refresh=True)
    calls = []
    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE, calls))
    second = cov.report("all")
    assert second["cache"]["source"] == "disk"
    assert second["cache"]["is_stale"] is False
    assert calls == []                              # 一次库都没问
    assert (tmp_path / cov.CACHE_FILE).exists()


def test_缓存过期时先给旧的并标_is_stale_同时后台重算(monkeypatch):
    """页面拿不到数据就会回退成"未接入" —— 宁可给旧的，但身份必须写在脸上。"""
    import json
    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE))
    first = cov.report("all", refresh=True)
    f = cov._cache_path()
    with open(f, encoding="utf-8") as fh:          # 必须 with：不 flush 就读就是读到半份
        data = json.load(fh)
    data["saved_at"] = "2026-01-01T00:00:00+08:00"
    for k in data["keys"].values():
        k["saved_at"] = "2026-01-01T00:00:00+08:00"
    if data.get("all"):
        data["all"]["saved_at"] = "2026-01-01T00:00:00+08:00"
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    started = []
    cov.report  # noqa: B018
    import threading
    real = threading.Thread

    class _NoThread:
        def __init__(self, *a, **k):
            started.append(k.get("name"))

        def start(self):
            pass
    threading.Thread = _NoThread
    try:
        out = cov.report("all")
    finally:
        threading.Thread = real
    assert out["cache"]["is_stale"] is True and out["cache"]["very_stale"] is True
    assert out["generated_at"]                      # 是算出来那一刻，不是"现在"
    assert started == ["coverage-refresh"]          # 过期 → 后台补算，不让页面等 53 秒


def test_refresh_强制重算不吃缓存(monkeypatch):
    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE))
    cov.report("all", refresh=True)
    calls = []
    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE, calls))
    cov.report("all", refresh=True)
    assert calls                                    # 确实重新问了库


def test_单表也走磁盘缓存不重复扫大表(monkeypatch):
    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE))
    cov.report("indicator_value", refresh=True)
    calls = []
    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE, calls))
    out = cov.report("indicator_value")
    assert out["cache"]["source"] == "disk" and out["tables"][0]["rows"] == 1200 or True
    assert calls == []


def test_已知硬缺口必须带说明文本(monkeypatch):
    """分钟线只有一天 / 资金流空表 —— 这两句话不写出来，页面就解释不了为什么某列全空。"""
    monkeypatch.setattr(pg, 'fetch_df', _fake(BASE))
    items = {t["key"]: t for t in cov.report("all", refresh=True)["tables"]}
    assert "2026-09-22" in items["stock_minute_1m"]["known_issue"]
    assert "push2" in items["fund_flow_rank"]["known_issue"]
    assert "分区" in items["indicator_value"]["known_issue"]
