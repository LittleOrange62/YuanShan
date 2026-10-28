"""组级自选分析（`duanxian/watchlist.py:group_analytics`）的口径回归。

全部打桩 `pg.fetch_df`：不连库。这里守的五件事都是实测踩过或核对过的：

1. **最小二乘的参数顺序** —— PG 的 `regr_slope(y, x)` 是 y 在前。第一版写成
   `regr_slope(rn, w_score)`，结果每组的"预测操作分"都落在 +10 附近
   （那其实是场次号的均值），趋势分数被压成 ±0.5。手算复核才发现，
   所以这条按 SQL 原文钉住，别再翻回去。
2. `up_ratio` 的分母是**当日有行情行的只数**，不是成员数；分母为 0 给 None，不给 0%。
3. 不足 10 场的组趋势三列是 NULL，且必须被计入 `trend_awaiting`（界面才说得出"有几组还没数"）。
4. 默认常亮的 3 条线按**斜率**选，不按当日分 —— 这张图问的是"谁在起来"。
5. **近 N 场的组间名次**（2026-10-26 用户点名要"每天的自选组结果搞一个排名"）：
   名次用加权分的**原值**（屏上两位小数会造出假并列）、真同分才同名并顺延、
   缺那一天就是 `·`/不给名次（不是最后一名）、`rank_now` 必须与「加权操作分」那一格同场。
"""

from __future__ import annotations

import pandas as pd
import pytest

from duanxian import pg, ranking, watchlist as wl

_COLS = ["group_id", "name", "is_focus", "display_order", "member_count", "w_score", "eq_score",
         "score_date", "trend_score", "pred_score", "trend_days", "avg_pct", "quoted_count",
         "up_count", "chg10_bp", "chg20_bp", "amp10_avg", "amp20_avg", "amp_pred",
         "scored_count", "dt_times"]

#: 打桩用的 5 个场次日期；末点与 `_row()` 默认的 `score_date` 同一天 ——
#: `rank_now` 只有在那一天才该有值，这两处必须咬住（否则测出来的是"名次与分数不同场"那种假读数）。
_RANK_DATES = [f"2026-09-{20 + i}" for i in range(5)]


def _row(gid, name, **over):
    r = dict(zip(_COLS, [gid, name, False, gid, 10, -20.0, -21.0, "2026-09-24",
                         1.0, 5.0, 20, -2.0, 10, 1, 10.0, 5.0, 4.0, 4.5, 4.2, 10, 3]))
    r.update(over)
    return r


def _manual_series(spec):
    """按 `_SERIES_SQL` 真实交出的列造序列：`spec = [(group_id, name, [(rn, 原值), ...])]`。

    ⚠️ 必须同时给 `w_score`（舍入到两位、屏上读的那份）与 `w_raw`（原值、排名用的那份）——
    桩只按真实契约长，否则"名次用原值排"这一条会在桩上永远测不到。
    """
    out = []
    for gid, name, points in spec:
        for rn, v in points:
            out.append({"group_id": gid, "name": name, "rn": rn, "trade_date": _RANK_DATES[rn - 1],
                        "w_score": round(float(v), 2), "w_raw": float(v)})
    return out


def _flat_series(rows, base=-40.0, step=1.0):
    """每个组 5 场都有分、组间大小按 group_id 递增（名次稳定，测缺天/母体时用）。"""
    return _manual_series([(r["group_id"], r["name"],
                            [(rn, base + step * r["group_id"] + rn) for rn in range(1, 6)])
                           for r in rows])


@pytest.fixture(autouse=True)
def _local_source_on(monkeypatch):
    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(pg, "latest_session", lambda table: "2026-09-24")


def _patch(monkeypatch, rows, series=None, level=None):
    series = series if series is not None else [
        {"group_id": r["group_id"], "name": r["name"], "trade_date": f"2026-08-{21 + i:02d}",
         "rn": i + 1, "w_score": round(-30.0 + i, 2), "w_raw": -30.0 + i + 0.004}
        for r in rows[:1] for i in range(3)]

    def fake(sql, params=()):
        if "regr_slope" in sql:
            return pd.DataFrame(rows)
        if "as lo" in sql:
            return pd.DataFrame([level or {"lo": -36.6, "hi": 21.9, "days": 20, "span": 58.5}])
        return pd.DataFrame(series)

    monkeypatch.setattr(pg, "fetch_df", fake)


# --------------------------------------------------------------------------- 参数顺序
def test_regression_is_fitted_on_score_given_session_index_not_the_reverse():
    sql = wl._GROUP_SQL
    assert "regr_slope(w_score::float8, rn::float8)" in sql
    assert "regr_intercept(w_score::float8, rn::float8)" in sql
    assert "regr_slope(rn::float8, w_score::float8)" not in sql, \
        "写反了：那会拟合出场次号对分数的斜率，预测分恒等于场次号均值附近"


# ---------------------------------------------------------------------------- 上涨占比
def test_up_ratio_divides_by_quoted_rows_and_stays_null_when_none(monkeypatch):
    rows = [_row(1, "甲", quoted_count=31, up_count=1),
            _row(2, "乙", quoted_count=0, up_count=0)]
    _patch(monkeypatch, rows)
    out = wl.group_analytics()
    assert out["rows"][0]["up_ratio"] == pytest.approx(3.2)
    assert out["rows"][1]["up_ratio"] is None, "分母 0 只能给 None，界面显示 `—`；给 0% 就是编"


def test_weighted_score_denominator_only_counts_rows_with_a_score():
    """分母必须是 `filter (where value is not null and amount is not null)` 的那部分成交额。

    写成裸 `sum(amount_yuan)` 会让"组里越多票没评分、加权分越被压低"。
    """
    sql = " ".join(wl._GROUP_SQL.split())
    assert "sum(d.amount_yuan) filter (where s.value is not null and d.amount_yuan is not null) as sw" in sql
    assert "from hist" in sql  # 加权分在组×场次上聚合，不是逐票


# ------------------------------------------------------------------------- 趋势不足
def test_groups_below_min_days_keep_null_trend_and_are_reported(monkeypatch):
    rows = [_row(1, "甲"), _row(2, "乙", trend_score=None, pred_score=None, trend_days=None)]
    _patch(monkeypatch, rows)
    out = wl.group_analytics()
    assert out["trend_ready"] == 1 and out["trend_awaiting"] == 1
    assert out["rows"][1]["trend_score"] is None
    assert any("不补 0" in c for c in out["caveats"])


def test_default_lines_are_the_three_steepest_not_the_best_scored(monkeypatch):
    rows = [_row(1, "陡1", trend_score=6.0, pred_score=-90.0),
            _row(2, "陡2", trend_score=5.0, pred_score=-90.0),
            _row(3, "陡3", trend_score=4.0, pred_score=-90.0),
            _row(4, "高分但退潮", trend_score=-9.0, pred_score=99.0)]
    _patch(monkeypatch, rows)
    out = wl.group_analytics()
    assert sorted(out["top_trend_groups"]) == sorted(["陡1", "陡2", "陡3"]), \
        "图按斜率选默认那几条：问的是谁在起来，不是谁最强"


def test_window_is_counted_in_sessions_not_calendar_days():
    assert "select distinct trade_date from analytics.score" in wl._GROUP_SQL
    assert wl.GROUP_WINDOW == 20 and wl.GROUP_MIN_DAYS == 10
    # 自然日圈窗会让长假一压只剩十来个点，斜率跨月不可比


# --------------------------------------------------------------------- 落库状态与单位
def test_payload_states_theres_no_group_table_and_shares_the_calibers(monkeypatch):
    _patch(monkeypatch, [_row(1, "甲")])
    out = wl.group_analytics()
    assert "没有一张组级表" in out["caveats"][0]
    assert set(out["calibers"]) >= {"w_score", "eq_score", "trend_score", "pred_score",
                                    "chg10_bp", "amp", "dt_times", "members"}
    assert out["unit"]["chg10_bp"] == "bp/日", "10/20 日均涨跌必须标 bp，标 % 会差 100 倍"
    assert out["source"] == "pg" and out["requests"] == 0
    assert isinstance(out["elapsed_ms"], int)


def test_missing_market_level_probe_does_not_invent_a_span(monkeypatch):
    def fake(sql, params=()):
        if "regr_slope" in sql:
            return pd.DataFrame([_row(1, "甲")])
        if "as lo" in sql:
            return None            # 探测失败
        return pd.DataFrame([])

    monkeypatch.setattr(pg, "fetch_df", fake)
    out = wl.group_analytics()
    assert out["market_level"] == {}
    assert "—" in next(c for c in out["caveats"] if "全市场相对分" in c)


def test_no_daily_rows_means_unavailable_not_an_empty_board(monkeypatch):
    monkeypatch.setattr(pg, "latest_session", lambda table: None)
    out = wl.group_analytics()
    assert out["available"] is False and out["reason"]


# ------------------------------------------------------------------- 近 N 场组间名次
def test_group_ranks_follow_the_daily_weighted_score_and_share_one_session_with_it(monkeypatch):
    """名次这把尺＝那一天组间加权分；且 `rank_now` 必须与「加权操作分」那一格是同一天。"""
    rows = [_row(1, "甲", w_score=-10.0), _row(2, "乙", w_score=-20.0), _row(3, "丙", w_score=-30.0)]
    vals = {1: -10.0, 2: -20.0, 3: -30.0}
    _patch(monkeypatch, rows, series=_manual_series(
        [(g, n, [(rn, vals[g]) for rn in range(1, 6)]) for g, n in ((1, "甲"), (2, "乙"), (3, "丙"))]))
    out = wl.group_analytics()
    by = {r["name"]: r for r in out["rows"]}
    assert [by[n]["rank_now"] for n in ("甲", "乙", "丙")] == [1, 2, 3]
    assert by["甲"]["rank_days"] == 5
    assert by["甲"]["rank_path"] == "1→1→1→1→1"
    assert out["rank"]["days"] == wl.GROUP_RANK_DAYS == 5
    assert out["rank"]["per_session"][-1]["trade_date"] == by["甲"]["score_date"], \
        "最近那一场必须就是表里加权分那一场，否则名次与分数不同源"
    assert out["rank"]["universe"] == {"min": 3, "max": 3}, "分母是当天排得出名次的组数，不是库里组数"


def test_ranks_use_the_raw_score_so_display_rounding_is_not_a_tie(monkeypatch):
    """两组只差 0.004：屏上两位小数是同一个数，原值有先后 ⇒ 名次必须分开。

    用舍入后的值排名＝把"同分同名"这条规则用在了不同分上（2026-10-26 实测近 5 场每天 0~2 行会撞上）。
    """
    rows = [_row(1, "甲", w_score=-10.0), _row(2, "乙", w_score=-10.0), _row(3, "丙", w_score=-30.0)]
    vals = {1: "甲", 2: "乙", 3: "丙"}
    raw = {1: -10.004, 2: -10.0, 3: -30.0}
    _patch(monkeypatch, rows, series=_manual_series(
        [(g, vals[g], [(rn, raw[g]) for rn in range(1, 6)]) for g in raw]))
    out = wl.group_analytics()
    by = {r["name"]: r for r in out["rows"]}
    assert (by["甲"]["rank_now"], by["乙"]["rank_now"]) == (2, 1), \
        "屏上都是 −10.0：原值 −10.0 比 −10.004 高 ⇒ 乙第 1、甲第 2，不许并成同名"
    assert by["甲"]["w_score"] == by["乙"]["w_score"] == -10.0, "屏上这一格确实是同一个数"
    assert out["rank"]["rounded_ties"] == 10, "5 场 × 2 行：假并列的行数要当场交出来，屏上那句才不是编的"


def test_true_ties_share_the_rank_and_the_next_one_jumps_ahead(monkeypatch):
    """真同分才同名，下一名按位置顺延（1,1,3）—— 规则原文与判据都读 `duanxian/ranking.py` 那一份。"""
    rows = [_row(1, "甲"), _row(2, "乙"), _row(3, "丙")]
    raw = {1: -10.0, 2: -10.0, 3: -30.0}
    _patch(monkeypatch, rows, series=_manual_series(
        [(g, {1: "甲", 2: "乙", 3: "丙"}[g], [(rn, raw[g]) for rn in range(1, 6)]) for g in raw]))
    out = wl.group_analytics()
    by = {r["name"]: r for r in out["rows"]}
    assert (by["甲"]["rank_now"], by["乙"]["rank_now"], by["丙"]["rank_now"]) == (1, 1, 3), \
        "并列第 1 ⇒ 下一个是第 3（standard competition），不是第 2"
    assert out["rank"]["rule"] == ranking.RULE_NOTE
    assert out["rank"]["rule_short"] == ranking.RULE_NOTE_SHORT
    assert out["rank"]["direction"] == ranking.DIRECTION_NOTE, "方向那句话也只住 ranking.py 一处"


def test_a_group_that_cannot_be_scored_that_day_gets_a_dot_not_a_last_place(monkeypatch):
    """那一天整组算不出加权分（没有一只票同时有分与成交额）→ 路径里 `·`、不占名次。"""
    rows = [_row(1, "甲"), _row(2, "乙")]
    series = [s for s in _flat_series(rows) if not (s["group_id"] == 2 and s["rn"] in (2, 3))]
    _patch(monkeypatch, rows, series=series)
    out = wl.group_analytics()
    by = {r["name"]: r for r in out["rows"]}
    assert by["乙"]["rank_path"] == "1→·→·→1→1"
    assert by["乙"]["rank_days"] == 3 and by["乙"]["rank_now"] == 1
    # 甲在乙缺席的那两场是当天唯一的组 ⇒ 第 1
    assert by["甲"]["rank_path"] == "2→1→1→2→2" and out["rank"]["partial"] == 1
    assert out["rank"]["no_rank"] == 0


def test_rank_gain_is_first_minus_latest_so_positive_means_moving_forward(monkeypatch):
    """净变化 = 最早那场 − 最近那场：正数＝名次数字变小（往前走）。方向写反这列就全反。"""
    rows = [_row(1, "后发先至"), _row(2, "先盛后衰")]
    _patch(monkeypatch, rows, series=_manual_series([
        (1, "后发先至", [(1, -10.0), (2, -10.0), (3, -10.0), (4, -10.0), (5, -10.0)]),
        (2, "先盛后衰", [(1, -20.0), (2, -5.0), (3, -5.0), (4, -5.0), (5, -5.0)])]))
    by = {r["name"]: r for r in wl.group_analytics()["rows"]}
    assert by["后发先至"]["rank_path"] == "1→2→2→2→2"
    assert by["后发先至"]["rank_gain"] == -1, "从第 1 掉到第 2 ⇒ 后退，净变化必须是负"
    assert by["先盛后衰"]["rank_path"] == "2→1→1→1→1"
    assert by["先盛后衰"]["rank_gain"] == 1, "从第 2 走到第 1 ⇒ 前进，净变化是正"


def test_rank_gain_never_uses_an_older_session_as_its_endpoint(monkeypatch):
    """最近那场排不出名次时净变化给 None：拿一次更早的名次当终点会被读成"最新"。"""
    rows = [_row(1, "甲"), _row(2, "乙")]
    series = _manual_series([
        (1, "甲", [(1, -30.0), (2, -30.0), (3, -30.0), (4, -30.0), (5, -30.0)]),
        (2, "乙", [(1, -10.0), (2, -10.0), (3, -10.0), (4, -10.0)])])   # 第 5 场（=score_date）没行
    _patch(monkeypatch, rows, series=series)
    by = {r["name"]: r for r in wl.group_analytics()["rows"]}
    assert by["乙"]["rank_now"] is None and by["乙"]["rank_gain"] is None
    assert by["乙"]["rank_path"] == "1→1→1→1→·" and by["乙"]["rank_days"] == 4


def test_a_group_with_no_series_rows_at_all_shows_dash_not_a_row_of_dots(monkeypatch):
    """一场名次都排不出的组：路径交 None（屏上 —），五个「·」会被读成"排到最后了"。"""
    rows = [_row(1, "甲"), _row(2, "新组建的")]
    _patch(monkeypatch, rows, series=_flat_series(rows[:1]))
    out = wl.group_analytics()
    by = {r["name"]: r for r in out["rows"]}
    assert by["新组建的"]["rank_now"] is None and by["新组建的"]["rank_path"] is None
    assert by["新组建的"]["rank_days"] == 0
    assert out["rank"]["no_rank"] == 1
    assert any("一场名次都排不出" in c for c in out["caveats"])


def test_the_board_states_its_own_universe_and_the_rounding_artifact(monkeypatch):
    """两条新 caveat 必须带当场的数：母体是"这张表里的组"、不是全市场；假并列行数现场数。"""
    rows = [_row(1, "甲"), _row(2, "乙"), _row(3, "丙")]
    raw = {1: -10.004, 2: -10.0, 3: -30.0}
    _patch(monkeypatch, rows, series=_manual_series(
        [(g, {1: "甲", 2: "乙", 3: "丙"}[g], [(rn, raw[g]) for rn in range(1, 5)]) for g in raw]))
    out = wl.group_analytics()
    uni = next(c for c in out["caveats"] if "组与组之间，不是全市场" in c)
    assert "最多 3 个" in uni and "没凑满" in uni
    tie = next(c for c in out["caveats"] if "按加权分的原值排的" in c)
    assert "8 行" in tie, "4 场 × 2 行"
    assert set(out["calibers"]) >= {"rank", "rank_path", "rank_gain"}


def test_raw_score_stays_internal_so_the_series_contract_does_not_change(monkeypatch):
    """`w_raw` 只为排名存在，交出去前要丢掉：屏上与折线读的都是那位小数的 w_score。"""
    rows = [_row(1, "甲")]
    _patch(monkeypatch, rows, series=_flat_series(rows))
    out = wl.group_analytics()
    assert "w_raw" not in out["series"][0]
    assert "w_raw" in wl._SERIES_SQL, "原值必须在 SQL 里就取出来，不能在 Python 里拿舍入值凑"
