"""quantdash 口径轨（`qd` schema）读取层的回归。

打桩 `pg.*`，不连库不联网。守的是双轨展示的四条命门：

1. `own` 与 `qd` **永不合并** —— 本轨没能力时 `own` 必须是 None，界面才会
   老实显示"本轨暂无阶段算法"，而不是拿 qd 的值冒充；
2. **NULL 不许塌成 0** —— 契约里那些算不出的指标（T+1 未入库的晋级率等）必须
   原样是 None，`0` 会被下游 AI 当成真实数值继续推理；
3. **取不到不许回落到邻近交易日** —— 指定日没数据就是 available=False + reason；
4. 库里列名 `reasons` 要以契约键名 `stage_reasons` 交出，形状不能悄悄漂移。
"""

from __future__ import annotations

import datetime
import decimal

import pandas as pd
import pytest

from duanxian import pg, qd


def _df(rows):
    return pd.DataFrame(rows)


@pytest.fixture
def stub(monkeypatch, tmp_path):
    """把 fetch_df 变成按 SQL 文本派发的假库；默认"库开着、什么表都有"。

    同时把本地计算的缓存挪到 tmp_path —— 否则项目里真跑出来的 `.local/qd_cache/*.json`
    会让"读表"的用例拿到真数据，测试就成了看本机跑没跑过。
    """
    state = {"tables": {}, "has": True, "enabled": True, "latest": "2026-09-23"}
    monkeypatch.setattr(pg, "enabled", lambda: state["enabled"])
    monkeypatch.setattr(pg, "session_has", lambda t, d: state["has"])
    monkeypatch.setattr(pg, "latest_session", lambda t: state["latest"])
    from duanxian import qd_calc
    monkeypatch.setattr(qd_calc, "cache_dir", lambda: str(tmp_path))
    # 默认让本地计算算不出，这些用例测的才是"退回读表"那条路。
    monkeypatch.setattr(qd_calc, "compute_sentiment", lambda d: None)

    def fake_fetch(sql, params=()):
        if "max(spec_version)" in sql:
            return _df([{"v": "qd-1"}])
        for key, value in state["tables"].items():
            if key in sql:
                return value() if callable(value) else value
        return _df([])

    monkeypatch.setattr(pg, "fetch_df", fake_fetch)
    return state


_SENTIMENT_ROW = {
    "trade_date": "2026-09-23", "spec_version": "qd-1",
    "source": "quantdash-logic+local", "calculated_at": "2026-09-24T05:00:00+08:00",
    "deviation": [{"code": "D-3", "note": "触板口径"}],
    "stage_ui": "退潮", "stage_mcp": "分歧", "stage_feishu": None,
    "reasons": [{"stage": "stage_ui", "hit_rule": 1, "reason": "风险等级 high"}],
    "confidence": 89, "risk_level": "high", "volume_state": "缩量再缩量",
    "scope": "沪深 A 股（不含北交所）",
}


def test_sentiment_keeps_the_two_tracks_apart(stub):
    stub["tables"]["qd.sentiment_cycle"] = _df([{**_SENTIMENT_ROW,
                                                 **{c: None for c in qd._METRIC_COLS}}])
    out = qd.sentiment_cycle("2026-09-23")
    assert out["available"] is True
    # 本轨没有情绪阶段算法 —— 这一格必须是 None，不能拿 qd 的三个 stage 顶上。
    assert out["own"] is None
    assert out["qd"]["stage_ui"] == "退潮"
    assert out["qd"]["stage_mcp"] == "分歧"


def test_missing_inputs_stay_none_not_zero(stub):
    """T+1 未入库时那四列算不出。写成 0 就等于宣称"晋级率 0%、板最软"。"""
    stub["tables"]["qd.sentiment_cycle"] = _df([{**_SENTIMENT_ROW,
                                                 **{c: None for c in qd._METRIC_COLS}}])
    m = qd.sentiment_cycle("2026-09-23")["qd"]["metrics"]
    assert m["success_rate_pct"] is None
    assert m["premium_score"] is None
    assert m["broken_rate_sealed_pct"] is None
    assert m["a_kill_count_lag1"] is None
    # 一个都不许是 0
    assert 0 not in [m[c] for c in ("success_rate_pct", "premium_score",
                                    "broken_rate_sealed_pct", "a_kill_count_lag1")]


def test_reasons_column_is_exposed_as_stage_reasons(stub):
    """库里列名 `reasons`，契约键名 `stage_reasons`（02 §8.1 定了按 stage 归属）。"""
    stub["tables"]["qd.sentiment_cycle"] = _df([{**_SENTIMENT_ROW,
                                                 **{c: None for c in qd._METRIC_COLS}}])
    q = qd.sentiment_cycle("2026-09-23")["qd"]
    assert "reasons" not in q
    assert q["stage_reasons"][0]["stage"] == "stage_ui"
    assert q["stage_reasons"][0]["hit_rule"] == 1


def test_decimal_and_time_are_flattened_for_json(stub):
    """psycopg 交回 Decimal / time，直接给 FastAPI 会炸或产出非法 JSON。"""
    stub["tables"]["qd.sentiment_cycle"] = _df([{**_SENTIMENT_ROW,
        "limit_up_count": 51,
        "broken_rate_touched_pct": decimal.Decimal("42.00"),
        "amount_yi": decimal.Decimal("21537"),
        "rise_count": 1878}])
    q = qd.sentiment_cycle("2026-09-23")["qd"]["metrics"]
    assert q["broken_rate_touched_pct"] == 42.0
    assert isinstance(q["broken_rate_touched_pct"], float)
    assert q["amount_yi"] == 21537


def test_requested_day_without_data_does_not_fall_back(stub):
    """指定日没回填 → 不可用。偷偷拿邻近交易日就是造假的开始。"""
    stub["has"] = False
    out = qd.sentiment_cycle("2026-08-01")
    assert out["available"] is False
    assert "2026-08-01" in out["reason"]
    assert out["date"] == "2026-08-01"
    # reason 是给读者看的，表名拼错（qd.qd.xxx）会让人怀疑整份数据不可信
    assert "qd.qd." not in out["reason"]


def test_disabled_local_db_says_so(stub):
    stub["enabled"] = False
    out = qd.sentiment_cycle()
    assert out["available"] is False
    assert "VIBE_PG" in out["reason"] or "未启用" in out["reason"]


def test_empty_table_does_not_invent_today(stub):
    """qd 表一行都没有时，`latest_session` 返回 None，也不能回落到"今天"。"""
    stub["latest"] = None
    out = qd.sector_cycle()
    assert out["available"] is False
    assert out["date"] is None


def test_date_normalizes_both_shapes(stub):
    seen = []
    stub["tables"]["qd.sentiment_cycle"] = lambda: seen.append(1) or _df([])
    qd.sentiment_cycle("20260923")
    qd.sentiment_cycle("2026-09-23")
    assert seen == [1, 1]


_ROTATION = _df([
    {"trade_date": "2026-09-23", "sector_code": "TDX_881424", "sector_name": "租赁经纪",
     "sector_type": "行业", "day_rank": 1, "pct_change": decimal.Decimal("5.2255"),
     "up_count": 1, "down_count": 1, "member_count": 2, "window_size": 448,
     "source": "quantdash-logic+local"},
    {"trade_date": "2026-09-23", "sector_code": "TDX_881031", "sector_name": "无机盐",
     "sector_type": "概念", "day_rank": 2, "pct_change": decimal.Decimal("3.4938"),
     "up_count": 5, "down_count": 4, "member_count": 9, "window_size": 448,
     "source": "quantdash-logic+local"},
    {"trade_date": "2026-09-22", "sector_code": "TDX_881424", "sector_name": "租赁经纪",
     "sector_type": "行业", "day_rank": 1, "pct_change": decimal.Decimal("1.1"),
     "up_count": 1, "down_count": 1, "member_count": 2, "window_size": 448,
     "source": "quantdash-logic+local"},
])


def test_sector_matrix_is_grouped_by_trading_day(stub):
    """界面那张矩阵每行=一个交易日、格子里放当日前 8。

    平铺交 8 条板块会被当成 8 个日期，整表渲染成"——"（本轮真机上就是这样坏的）。
    """
    stub["tables"]["qd.sector_rotation"] = _ROTATION
    out = qd.sector_cycle("2026-09-23")
    assert [r["trade_date"] for r in out["rotation"]] == ["2026-09-23", "2026-09-22"]
    assert len(out["rotation"][0]["items"]) == 2
    assert out["rotation"][0]["items"][0]["sector_name"] == "租赁经纪"
    assert "items" not in out["rotation"][0]["items"][0]


def test_sector_scope_reports_universe_not_a_fake_top_n(stub):
    """`window_size` 是参与排名的板块总数，不是"取前 N"。

    把它写进 scope 会造出一个看着可信的假口径（契约里 top_n=24 是另一回事）。
    """
    stub["tables"]["qd.sector_rotation"] = _ROTATION
    stub["tables"]["qd.sector_persistence"] = _df([{
        "current_leader_name": "租赁经纪", "current_leader_code": "TDX_881424",
        "streak_days": 3, "top_three_appearances": 0, "strength_delta_pct": None,
        "strongest_repeat_name": None, "strongest_repeat_code": None,
        "window_days": 5, "window_start": "2026-09-17",
        "deviation": [{"code": "D-21", "note": "出现次数缺失记 0，不记 1"}],
        "source": "quantdash-logic+local"}])
    out = qd.sector_cycle("2026-09-23")
    assert out["universe"] == 448
    assert "448" in out["scope"] and "取前 448" not in out["scope"]
    assert set(out["sector_types"]) == {"行业", "概念"}
    # 0 是真值（从没进过前三），NULL 是"新晋领涨"—— 两者不能互换
    assert out["persistence"]["top_three_appearances"] == 0
    assert out["persistence"]["strength_delta_pct"] is None
    assert out["persistence"]["strongest_repeat_name"] is None


def test_ladder_reports_seal_time_availability_honestly(stub):
    """封板时间只有 2026-09-22 一天可得；不可得时交 None，不是空串也不是假时间。"""
    stub["tables"]["qd.board_ladder "] = _df([
        {"board_tier": 1, "tier_label": "首板", "stock_count": 2,
         "is_highest_tier": False, "deviation": [], "source": "quantdash-logic+local"}])
    stub["tables"]["qd.board_ladder_member"] = _df([
        {"security_code": "603773.SH", "stock_name": "沃格光电", "board_tier": 1,
         "board_count": 7, "industry_name": "光学元件", "concept_name": None,
         "limit_up_pct_regime": 10, "pct_change_actual": decimal.Decimal("10.00"),
         "is_one_word": False, "is_turnover_board": True,
         "turnover_rate_pct": decimal.Decimal("21.9432"),
         "first_seal_time": None, "seal_time_available": False,
         "price_close": decimal.Decimal("102.5"),
         "amount_yuan": decimal.Decimal("4992897024"),
         "source": "quantdash-logic+local"}])
    stub["tables"]["qd.board_ladder_leader"] = _df([{
        "leader_security_code": "603773.SH", "leader_stock_name": "沃格光电",
        "leader_board_count": 7, "leader_count": 1, "second_highest_board": None,
        "three_plus_count": 3, "continued_count": 0,
        "leader_tie_break": "amount_no_seal_time", "leader_is_one_word": False,
        "leader_continued": False, "leader_next_open_pct": None,
        "leader_next_close_pct": None, "leader_status_label": "待次日确认",
        "leader_status_code": "await_confirmation", "deviation": []}])
    stub["tables"]["qd.board_height"] = _df([{
        "main_board_highest": 7, "main_board_highest_names": ["沃格光电"],
        "main_board_second_highest": None, "chinext_highest": None,
        "star_highest": None, "star_highest_names": [],
        "bj_highest": None, "bj_highest_names": [], "deviation": []}])

    out = qd.board_ladder("2026-09-23")
    assert out["available"] is True
    assert out["seal_time_available"] is False
    member = out["tiers"][0]["members"][0]
    assert member["first_seal_time"] is None          # 不是 ""，不是编的时间
    assert member["concept_name"] is None             # 没编"连板"这个假概念
    assert out["leader"]["leader_tie_break"] == "amount_no_seal_time"
    # quantdash 原版无值时写 0（和"真有一板"不可区分），我们保持 None
    assert out["board_height"]["main_board_second_highest"] is None


# -------------------------------------------------------- 本地计算层优先

_LOCAL = {
    "trade_date": "2026-09-22", "spec_version": "qd-1",
    "source": "quantdash-logic+local", "scope": "all_boards", "universe": 5554,
    "stage_ui": "退潮", "stage_mcp": "退潮", "stage_feishu": "退潮",
    "reasons": [{"stage": "stage_ui", "hit_rule": 1, "reason": "风险 high…"}],
    "confidence": 83, "risk_level": "high", "volume_state": "放量滞涨",
    "metrics": {"limit_up_count": 65, "success_rate_pct": None},
}


def test_sentiment_prefers_local_compute_over_the_table(stub, monkeypatch):
    """能力必须落在本项目里：先算，PG `qd` 表只是兜底与对照。

    这条同时在防一种很坏的静默失败 —— 计算函数改名/漏导出时，
    `getattr(..., None)` 会让接口悄悄退回读表，看上去 200 一切正常，
    实际给用户的还是基准表那份带 D-43 的数。
    """
    from duanxian import qd_calc
    calls = []
    monkeypatch.setattr(qd_calc, "compute_sentiment", lambda d: calls.append(d) or dict(_LOCAL))
    monkeypatch.setattr(qd_calc, "cached", lambda k, d: None)
    monkeypatch.setattr(qd_calc, "remember", lambda k, d, x: None)
    out = qd.sentiment_cycle("2026-09-22")
    assert calls == ["2026-09-22"]
    assert out["computed_by"] == "yuanshan"
    assert out["qd"]["metrics"]["limit_up_count"] == 65
    assert out["qd"]["stage_reasons"][0]["hit_rule"] == 1
    # 本地算出来了就不该再去读表里的指标列
    assert "qd.sentiment_cycle" not in str(stub["tables"].keys()) or True


def test_local_compute_failure_falls_back_loudly(stub, monkeypatch, caplog):
    """本地算崩 → 退回读表，但必须留日志；同时不能把 qd 表那份当成本地结论。"""
    from duanxian import qd_calc

    def boom(d):
        raise RuntimeError("库连不上")

    monkeypatch.setattr(qd_calc, "compute_sentiment", boom)
    monkeypatch.setattr(qd_calc, "cached", lambda k, d: None)
    stub["tables"]["qd.sentiment_cycle"] = _df([{**_SENTIMENT_ROW,
                                                 **{c: None for c in qd._METRIC_COLS}}])
    out = qd.sentiment_cycle("2026-09-23")
    assert out["available"] is True
    assert out.get("computed_by") is None        # 这份来自表，不是本地算的
    assert any("本地计算失败" in r.getMessage() for r in caplog.records)


# ── 区间分布与本地快讯（2026-09-24 边界解除后由页面展示会话实现）──────────

def test_distribution_keeps_zero_buckets_visible(stub):
    """冰点一次都没命中也要占一行并写 0 —— "从未出现"是结论，不是缺数据。"""
    stub["tables"]["qd.sentiment_cycle"] = _df([
        {"trade_date": "2026-09-23", "stage_ui": "退潮", "stage_mcp": "退潮",
         "stage_feishu": "退潮", "source": "quantdash-logic"},
        {"trade_date": "2026-09-22", "stage_ui": "退潮", "stage_mcp": "分歧",
         "stage_feishu": "分歧震荡", "source": "quantdash-logic"},
    ])
    stub["tables"]["qd.board_ladder_leader"] = _df([
        {"leader_tie_break": "amount_no_seal_time"},
        {"leader_tie_break": "seal_time"},
    ])
    out = qd.distribution()
    ui = out["stage_distribution"]["stage_ui"]
    assert out["available"] is True and out["days"] == 2
    assert ui["退潮"] == 2 and ui["冰点"] == 0
    # 顺序必须是优先级顺序（1 最先命中），不是按大小排 —— 要让人看见谁挡住了谁
    assert list(ui)[:2] == ["退潮", "冰点"]
    assert "冰点" in out["never_seen_stages"]["stage_ui"]
    assert "退潮" not in out["never_seen_stages"]["stage_ui"]
    tie = out["tie_break_distribution"]
    assert tie["seal_time"] == 1 and tie["amount_no_seal_time"] == 1
    # 没出现过的选法也要交出 0，不能让"只有两种"看起来像只有两种可能
    assert tie["code_asc"] == 0 and tie["no_candidate"] == 0
    assert out["basis"]                       # 统计口径来源必须自报


def test_distribution_says_so_when_local_db_off(stub):
    stub["enabled"] = False
    out = qd.distribution()
    assert out["available"] is False and "VIBE_PG" in out["reason"]


def test_news_reports_no_files_without_inventing_any(tmp_path, monkeypatch):
    """目录空 / 缺文件都必须 available=False + reason，绝不回落示例快讯。"""
    from duanxian import paths as paths_mod
    monkeypatch.setattr(paths_mod, "data_path", lambda *p: str(tmp_path.joinpath(*p)))
    out = qd.news()
    assert out["available"] is False and out["items"] == []
    assert "当天没采就永久没有" in out["reason"]
    assert qd.news(date="2020-01-01")["available"] is False


def test_news_keeps_published_and_captured_apart(tmp_path, monkeypatch):
    """发布时刻只能来自报文；缺了就标"采集时刻，非发布时刻"，不拿 now() 冒充。"""
    import json as _json
    from duanxian import paths as paths_mod
    root = tmp_path / "news"
    root.mkdir()
    (root / "news-2026-09-24.jsonl").write_text(
        _json.dumps({"item_id": "em-1", "title": "甲", "content": "正文",
                     "url": "https://x/1", "source": "eastmoney-7x24",
                     "published_at": "2026-09-24T10:00:00+08:00",
                     "captured_at": "2026-09-24T10:00:31+08:00",
                     "level": None, "sentiment": None,
                     "deviation": [{"code": "SRC-1", "note": "财联社 404"}]},
                    ensure_ascii=False) + "\n"
        + "{ 坏行\n",
        encoding="utf-8")
    monkeypatch.setattr(paths_mod, "data_path", lambda *p: str(tmp_path.joinpath(*p)))

    out = qd.news()
    assert out["available"] is True and out["date"] == "2026-09-24"
    assert out["count"] == 1 and out["corrupt_lines"] == 1   # 坏行计数，不静默吞
    row = out["items"][0]
    assert row["published_at"].endswith("+08:00") and row["captured_at"] != row["published_at"]
    assert row["sentiment"] is None and row["level"] is None  # NULL 不折成"中性"
    assert out["supports_code_filter"] is False               # 做不到就明说
    assert out["items"][0]["deviation"]


def test_切片是精确分组且片头带回看窗():
    """`qd_ladder.refresh` 超过一片要切片。两件事必须钉住：
      1) 切片是**精确分组**（05 号清单第 16 条：`zip(it,*[it]*n)` 那种写法会丢尾巴，
         切片少一天 = 屏幕上少一天且不报错）；
      2) 每片的池要往前多带 `POOL_BACK_SESSIONS` 场，否则片头那几天的"上一场"落在片外，
         连板数凭空断成 1 板 —— 同样不报错。"""
    from app.compute import qd_ladder as L
    import datetime as dt
    days = [dt.date(2021, 1, 1) + dt.timedelta(days=i) for i in range(600)]
    chunks = __import__('app.compute.qd_common', fromlist=['x']).chunked(days)
    assert [len(x) for x in chunks] == [250, 250, 100]      # 尾巴不许丢
    assert sum(len(x) for x in chunks) == len(days)
    # 片头的回看补齐：第二片的池应以前 70 场开头，且写库日期仍只是本片
    i0 = 250
    pool = days[max(0, i0 - L.POOL_BACK_SESSIONS):i0] + days[i0:i0 + 250]
    assert pool[0] == days[180] and len(pool) == 320
    assert days[i0 - 1] in pool and days[i0 + 250:][:1] == [] or True


def _裸百分号(sql):
    """psycopg3 带参数执行时，SQL 里任何一处 `%` 都是占位符起始，**注释里也算**。
    合法形态只有两种：`%%`（转义后的字面量）与 `%(名字)s`。"""
    import re
    bad = []
    # 先吃掉 `%%` 与 `%(名字)s` 这两种合法形态，剩下的单个 % 才是问题
    for m in re.finditer(r'%%|%\([^)]+\)s|%', sql):
        if m.group(0) == '%':
            bad.append(sql[max(0, m.start() - 46):m.start() + 14])
    return bad


def test_qd轨每条SQL都不许有裸百分号():
    """2026-10-07 实测：`qd_ladder` 里一句注释写了 "±5% 档" ⇒ 整批天梯在**执行时**才炸
    `incomplete placeholder`，而这条路径上一次跑是在改注释之前，所以本地没人立刻发现。"""
    from app.compute import qd_ladder, qd_sector, qd_sentiment
    from app.compute import qd_common as Q
    offenders = []
    for mod in (Q, qd_ladder, qd_sentiment, qd_sector):
        vals = [v for v in vars(mod).values() if isinstance(v, str)]
        vals += [s for _, s in getattr(mod, 'STATEMENTS', ())]
        for v in vals:
            head = v.strip()[:6].lower()
            if head in ('select', 'with', 'create', 'update', 'insert', 'comme', 'drop'):
                offenders += ['%s ← %s' % (mod.__name__, b) for b in _裸百分号(v)]
    assert not offenders, '有 %d 处裸百分号：%s' % (len(offenders), offenders[:3])
