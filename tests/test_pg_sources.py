"""本地 PostgreSQL（financetool）P0 取数层的形状与口径回归。

全部打桩 `pg.fetch_df` / `pg.session_has`：不连库、不联网（`conftest.py` 本就拦出站）。
这里守的是两件事：① 返回结构必须和它要替代的在线取数**完全同形**，否则上层
`market_facts` / `breadth` 会静默读到不匹配的字段；② 失败必须回 None 让调用方回退，
不能把"取不到"伪装成 0。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from duanxian import breadth as breadth_mod
from duanxian import pg, pg_sources


@pytest.fixture(autouse=True)
def _local_source_on(monkeypatch):
    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(pg, "session_has", lambda table, date: True)


def _patch_df(monkeypatch, df):
    monkeypatch.setattr(pg, "fetch_df", lambda sql, params=(): df)


# --------------------------------------------------------------------------- 宽度
_BREADTH_COLS = ("up", "down", "flat", "amount", "universe", "up5", "down5",
                 "limit_up", "limit_down", "touched", "broken")


def _breadth_row(**over):
    row = dict(zip(_BREADTH_COLS, [2277, 2789, 142, 2.1354e12, 5554, 233, 49,
                                   65, 4, 102, 37]))
    row.update(over)
    return pd.DataFrame([row])


def test_breadth_payload_satisfies_the_online_validator(monkeypatch):
    """关键约束：`breadth._payload_ok` 是联网路径自己的守门人。

    本地那份必须原样通过它，否则 `_from_pg` 会永远判定"不合格"而回退在线源 ——
    那种失败是静默的，看起来像"本地源没生效"。
    """
    _patch_df(monkeypatch, _breadth_row())
    b = pg_sources.breadth("20260922")
    assert b is not None and breadth_mod._payload_ok(b)
    assert b["up"] == 2277 and b["universe"] == 5554
    # 成交额：元 → 亿
    assert b["amount_yi"] == 21354.0
    # 口径写进键名与文本，不靠注释
    assert b["source"] == "pg" and "本地日线" in b["up_down_scope"]
    assert b["requests"] == 0


def test_breadth_denominator_is_touched_not_pool(monkeypatch):
    """炸板率的分母是「触板」，和 `seal_quality` 的 炸板池/(涨停+炸板池) 不是一回事。

    09-22 实测：触板 102 / 封住 65 → 37 家炸板、36.27%。
    若哪天有人把它接到 seal_quality 的键上，这个断言会先红。
    """
    _patch_df(monkeypatch, _breadth_row())
    b = pg_sources.breadth("20260922")
    assert b["broken_of_touched_count"] == 37
    assert b["broken_of_touched_rate"] == pytest.approx(37 / 102 * 100, abs=.01)
    assert "broken_rate" not in b            # 别和在线那个同名键混用


def test_breadth_without_touches_reports_none_rate_not_zero(monkeypatch):
    _patch_df(monkeypatch, _breadth_row(touched=0, broken=0, limit_up=0))
    b = pg_sources.breadth("20260922")
    assert b["broken_of_touched_rate"] is None    # 「没人触板」≠ 炸板率 0
    assert b["broken_of_touched_count"] == 0


def test_breadth_is_none_when_the_session_is_missing(monkeypatch):
    monkeypatch.setattr(pg, "session_has", lambda table, date: False)
    assert pg_sources.breadth("20260922") is None


def test_breadth_is_none_on_empty_or_failed_query(monkeypatch):
    _patch_df(monkeypatch, None)
    assert pg_sources.breadth("20260922") is None
    _patch_df(monkeypatch, pd.DataFrame([dict(zip(_BREADTH_COLS, [0] * 11))]))
    assert pg_sources.breadth("20260922") is None   # universe=0 → 不是这一场的数


@pytest.fixture
def _settled(monkeypatch):
    """`_from_pg` 只在已收盘的场次生效，所以测它时必须先让日历说是。"""
    monkeypatch.setattr(breadth_mod.trade_calendar, "is_settled", lambda d: True)
    monkeypatch.setattr(breadth_mod, "_cache_path", lambda d: "/tmp/astock-breadth-test.json")


def test_from_pg_falls_back_when_disabled(_settled, monkeypatch):
    monkeypatch.setattr(pg, "enabled", lambda: False)
    assert breadth_mod._from_pg("20260922") is None


def test_from_pg_falls_back_when_local_source_raises(_settled, monkeypatch):
    def boom(date):
        raise RuntimeError("库连不上")

    monkeypatch.setattr(pg_sources, "breadth", boom)
    assert breadth_mod._from_pg("20260922") is None


def test_from_pg_rejects_malformed_payload(_settled, monkeypatch):
    """过不了守门人的本地结果必须丢弃，不能让半个数进 prompt。"""
    _patch_df(monkeypatch, _breadth_row())
    real = pg_sources.breadth
    monkeypatch.setattr(pg_sources, "breadth",
                        lambda date: {**real(date), "up": None})
    assert breadth_mod._from_pg("20260922") is None


def test_local_source_does_not_answer_unsettled_sessions(monkeypatch):
    """拒绝语义是产品不变量：盘中/更早场次 `market_breadth` 原本就返回 available=False。

    本地日线**不许抢答**这些场次 —— 否则"过期行情不冒充当场快照"这条规则
    会在一次换数据源的改动里被悄悄换掉。这里用库里有数的真实历史日期来验。
    """
    _patch_df(monkeypatch, _breadth_row())
    monkeypatch.setattr(breadth_mod.trade_calendar, "is_settled", lambda d: False)
    assert breadth_mod._from_pg("20260922") is None


# --------------------------------------------------------------------- 板块周期
_CYCLE_COLS = ("code", "name", "stype", "stage", "score", "squeeze", "position_score",
               "ignition", "volume", "breadth", "term_count", "heat_pctile", "slope3",
               "vol_ratio", "share_ratio", "up_ratio")


def _cycle_df():
    """三行：真题材 / 通达信行为统计板块 / 聚合指标板块 —— 顺序即库返回顺序。"""
    rows = [
        ("TDX_880328", "黄金", "概念", "coil", 50.47, 76.5, 90.0, 70.0, 0.0, 0.0, 5,
         "0.1", "2.68", "0.38", "0.42", "0.2"),
        ("TDX_880963", "昨日突涨", "风格", "coil", 54.21, 72.84, 87.5, 30.0, 83.94, 0.0, 5,
         "0.12", "-1.6", "1.68", "1.81", "0.2"),
        ("TDX_880082", "板块趋势", "风格", "fade", 38.9, 72.65, 95.0, 30.0, 0.0, None, 4,
         "0.9", "-3.0", "0.5", "0.6", "0.1"),
    ]
    return pd.DataFrame(rows, columns=_CYCLE_COLS)


def test_sector_cycle_keeps_the_five_elements_explainable(monkeypatch):
    """页面要能回答"凭什么说它点火" → 五要素必须分开交出去，不能只剩综合分。"""
    _patch_df(monkeypatch, _cycle_df())
    rows = pg_sources.sector_cycle("2026-09-23")
    assert [r["code"] for r in rows] == ["TDX_880328", "TDX_880963", "TDX_880082"]
    gold = rows[0]
    assert gold["stage"] == "coil" and gold["sector_type"] == "概念"
    assert gold["elements"] == {"squeeze": 76.5, "position": 90.0,
                                "ignition": 70.0, "volume": 0.0, "breadth": 0.0}
    assert gold["heat"]["slope3"] == 2.68
    # 广度算不出（NULL）时必须是 None，不能悄悄变 0 —— 0 分是"板块内没人涨"，
    # 而 None 是"这个板块根本没有涨跌家数"，两回事。
    assert rows[2]["elements"]["breadth"] is None
    assert rows[2]["term_count"] == 4


def test_sector_cycle_marks_statistical_sectors_not_themes(monkeypatch):
    """"昨日突涨正在蓄势"是同义反复：行为统计板块与聚合指标板块要打标，但**不删**。"""
    _patch_df(monkeypatch, _cycle_df())
    rows = pg_sources.sector_cycle("2026-09-23")
    assert [r["synthetic"] for r in rows] == [False, True, True]
    assert len(rows) == 3                      # 打标不等于裁掉，交给界面按口径筛


def test_sector_cycle_is_none_without_the_session(monkeypatch):
    monkeypatch.setattr(pg, "session_has", lambda table, date: False)
    assert pg_sources.sector_cycle("2026-09-23") is None
    monkeypatch.setattr(pg, "session_has", lambda table, date: True)
    _patch_df(monkeypatch, None)
    assert pg_sources.sector_cycle("2026-09-23") is None
    _patch_df(monkeypatch, pd.DataFrame(columns=_CYCLE_COLS))
    assert pg_sources.sector_cycle("2026-09-23") is None


# --------------------------------------------------------------------- 技术指标
@pytest.mark.parametrize("raw,expected", [
    ("600519", "600519.SH"), ("688981", "688981.SH"),
    ("000001", "000001.SZ"), ("300750", "300750.SZ"),
    ("830799", "830799.BJ"), ("920298", "920298.BJ"),
    ("600519.SH", "600519.SH"),        # 已带后缀不重复加工
    ("abc", "abc"),                     # 不是代码就原样交回，让库里查不到去解释
])
def test_full_code_matches_the_regime_case_rule(raw, expected):
    """六位→全码的分市场判据必须和 `_REGIME_CASE`（涨跌幅档位）同源，
    否则同一只票在两个函数里会被认成不同市场。"""
    assert pg_sources.full_code(raw) == expected


def _ind_df():
    return pd.DataFrame([
        ("ma20", "MA20", "yuan", 4, 1284.103, "2026-09-23"),
        ("pos250", "250日位置", "pct", 4, 24.037, "2026-09-23"),
        ("vol_ratio", "量比", "ratio", 4, None, "2026-09-23"),
    ], columns=["key", "name", "unit", "decimals", "value", "d"])


def test_indicators_keep_name_unit_and_null_value(monkeypatch):
    _patch_df(monkeypatch, _ind_df())
    out = pg_sources.indicators("600519", "2026-09-23")
    assert out["available"] is True and out["code"] == "600519.SH"
    assert out["count"] == 3 and out["source"] == "pg"
    assert out["indicators"]["ma20"] == {"name": "MA20", "unit": "yuan",
                                        "value": 1284.103, "decimals": 4}
    # 量比算不出时是 None，不能折成 0 —— 0 是"缩量到没有"，两回事
    assert out["indicators"]["vol_ratio"]["value"] is None


def test_indicators_fall_back_to_the_latest_session(monkeypatch):
    _patch_df(monkeypatch, _ind_df())
    seen = {}

    def fake(sql, params=()):
        seen["params"] = params
        return _ind_df()

    monkeypatch.setattr(pg, "fetch_df", fake)
    monkeypatch.setattr(pg, "latest_session", lambda table: "2026-09-23")
    out = pg_sources.indicators("600519")
    assert out["date"] == "2026-09-23"
    assert seen["params"] == ("600519.SH", "2026-09-23")


def test_indicators_none_when_missing(monkeypatch):
    _patch_df(monkeypatch, None)
    assert pg_sources.indicators("600519", "2026-09-23") is None
    _patch_df(monkeypatch, pd.DataFrame(columns=["key", "name", "unit", "decimals", "value", "d"]))
    assert pg_sources.indicators("999999", "2026-09-23") is None


# ------------------------------------------------------------------------- 龙虎榜
def test_lhb_fills_institution_net_that_online_cannot(monkeypatch):
    """在线 `fetch_lhb` 的 `inst_net` 恒为 None（接口没这列）。

    本地有逐日机构汇总 —— 单位是元，要换算成亿并与 net_buy 同精度。
    """
    _patch_df(monkeypatch, pd.DataFrame([
        {"code": "301047", "name": "义翘神州", "net_buy": 3.67e8,
         "reason": "日涨幅达到15%的前5只证券", "inst_net": -3.2e7},
        {"code": "603068", "name": "博通集成", "net_buy": 2.25e8,
         "reason": "连续三个交易日", "inst_net": None},
    ]))
    rows = pg_sources.lhb("20260922", top=2)
    assert [r["code"] for r in rows] == ["301047", "603068"]
    assert rows[0]["inst_net"] == -0.32        # 元 → 亿
    assert rows[1]["inst_net"] is None          # 没上榜机构 → None，不是 0
    assert rows[0]["net_buy"] == 3.67
    assert set(rows[0]) == {"code", "name", "net_buy", "reason", "inst_net"}


# --------------------------------------------------------------------- 交易日判据
# 2026-10-07 补的自判：唯一的日历源（新浪 tool_trade_date_hist_sina）实测只登到
# 2026-12-31，2027 年起会整段"查不到那一行"。钉的是**没行时只许判周末** ——
# 工作日仍旧必须交 None，读成"是交易日"等于让链在长假里天天空跑并落一堆失败。

def _no_calendar_row(monkeypatch):
    monkeypatch.setattr(pg, "fetch_df", lambda sql, params=(): pd.DataFrame(columns=["is_open"]))


def test_calendar_missing_saturday_is_closed(monkeypatch):
    """2026-10-10 = 周六（实测 weekday=5）。交易所周末不开市与调休无关，这是可证的。"""
    _no_calendar_row(monkeypatch)
    assert pg.trade_day_open("2026-10-10") is False
    assert pg.trade_day_open("20261011") is False        # 紧凑格式走同一个入口


def test_calendar_missing_weekday_is_not_a_trade_day(monkeypatch):
    """2027-05-05 = 周三，真上是劳动节休市；2027-01-04 = 周一。两个都判不了。"""
    _no_calendar_row(monkeypatch)
    assert pg.trade_day_open("2027-05-05") is None
    assert pg.trade_day_open("2027-01-04") is None


def test_calendar_row_beats_the_weekend_rule(monkeypatch):
    """行在 → 一切照行，自判不参与（免得哪天日历改了口径，页面还按周末说话）。"""
    seen = []

    def fake(sql, params=()):
        seen.append(params)
        return pd.DataFrame([{"is_open": True}])

    monkeypatch.setattr(pg, "fetch_df", fake)
    assert pg.trade_day_open("2026-10-10") is True
    assert seen == [("2026-10-10",)]


def test_query_failure_shares_the_weekend_answer(monkeypatch):
    """连不上库 / 查询报错（fetch_df 回 None）与"那一行不存在"是同一个分支：
    周末那条结论不依赖日历，所以照样给得出来；工作日给 None。"""
    monkeypatch.setattr(pg, "fetch_df", lambda sql, params=(): None)
    assert pg.trade_day_open("2026-10-10") is False
    assert pg.trade_day_open("2026-10-08") is None       # 周四，真的是交易日，但没证据


def test_row_with_null_flag_is_judge_not_at_all(monkeypatch):
    """行在、`is_open` 是 NULL = 数据坏了，不是"不开市"。"""
    monkeypatch.setattr(pg, "fetch_df",
                        lambda sql, params=(): pd.DataFrame([{"is_open": None}]))
    assert pg.trade_day_open("2026-10-10") is None


# ------------------------------------------------ 第四层：行情时间戳自证（2026-10-08 补）
# 用户要的是"后续程序可以自己去判断"，不是每年年底人来试。依据很硬：
# 交易所不开市的那天，实时行情的时间戳就停在上一个交易日（实测腾讯 `f[30]`）。
# 这里钉的是**这条证据的使用边界**：只对今天说话、09:31 前不作否定、探不到不猜、
# 不许翻日历的案、测试进程绝不真发请求。`now` 一律显式传，免得零点/开盘前后翻面。

def _row_missing_and_quote(monkeypatch, quote):
    _no_calendar_row(monkeypatch)
    monkeypatch.setattr(pg, "quote_trade_date", quote)


def test_quote_stamp_can_answer_a_day_the_calendar_does_not_have(monkeypatch):
    """2027-01-04（周一）日历没有那一行，行情时间戳就是那天 → 判**开市**，链照跑。"""
    _row_missing_and_quote(monkeypatch, lambda: "2027-01-04")
    now = dt.datetime(2027, 1, 4, 15, 30)
    assert pg.trade_day_open("2027-01-04", now) is True
    assert pg.trade_day_evidence("2027-01-04", now) == "quote"


def test_stale_quote_stamp_says_the_market_is_shut(monkeypatch):
    """长假里的工作日：时间戳还停在上一交易日 → 自己认出今天休市，不用人守。"""
    _row_missing_and_quote(monkeypatch, lambda: "2026-12-31")
    now = dt.datetime(2027, 1, 4, 15, 30)
    assert pg.trade_day_open("2027-01-04", now) is False
    assert pg.trade_day_evidence("2027-01-04", now) == "quote"


def test_the_layer_only_answers_for_today(monkeypatch):
    """报价时间戳只能证明"此刻在不在交易"。判一个还没到的日子＝猜，所以照实 None。"""
    _row_missing_and_quote(monkeypatch, lambda: "2027-01-04")
    assert pg.trade_day_open("2027-05-05", dt.datetime(2027, 1, 4, 15, 30)) is None


def test_before_open_a_stale_stamp_is_not_evidence(monkeypatch):
    """09:31 之前时间戳是昨天**是正常的**（今天还没开盘）。在这之前判"今天休市"
    会把交易日误判成休市日、整条链一天不跑 —— 这条闸必须留在判据里。"""
    _row_missing_and_quote(monkeypatch, lambda: "2027-01-01")
    assert pg.trade_day_open("2027-01-04", dt.datetime(2027, 1, 4, 9, 5)) is None
    assert pg.trade_day_open("2027-01-04", dt.datetime(2027, 1, 4, 9, 31)) is False


def test_no_probe_means_still_no_answer(monkeypatch):
    """探测失败（断网、接口改版）= 判不了，绝不退化成"那就是休市"或"那就是开市"。"""
    _row_missing_and_quote(monkeypatch, lambda: None)
    assert pg.trade_day_open("2027-01-04", dt.datetime(2027, 1, 4, 15, 30)) is None


def test_the_calendar_always_beats_the_quote(monkeypatch):
    """日历明说开市，行情时间戳是别的日期也不许翻案（两个源打架时口径只认日历）。"""
    def fake(sql, params=()):
        return pd.DataFrame([{"is_open": True}])

    monkeypatch.setattr(pg, "fetch_df", fake)
    monkeypatch.setattr(pg, "quote_trade_date", lambda: "2026-12-31")
    now = dt.datetime(2027, 1, 4, 15, 30)
    assert pg.trade_day_open("2027-01-04", now) is True
    assert pg.trade_day_evidence("2027-01-04", now) == "calendar"


def test_weekend_layer_runs_before_the_quote_and_skips_the_network(monkeypatch):
    """周六根本不需要出门探测：省一次外部请求，也让"判不了"少一层噪声。"""
    def boom():
        raise AssertionError("周末那一层已经答了，不该再去发请求")

    _row_missing_and_quote(monkeypatch, boom)
    now = dt.datetime(2027, 1, 2, 15, 30)
    assert pg.trade_day_open("2027-01-02", now) is False
    assert pg.trade_day_evidence("2027-01-02", now) == "weekend"


def test_null_row_lets_only_the_quote_speak_not_the_weekday(monkeypatch):
    """日历**有**那一天但值是 NULL = 数据坏了：不拿星期几补口径（既有那条
    `test_row_with_null_flag_is_judge_not_at_all` 钉的就是这个保守），
    但今天可以用行情自证。"""
    monkeypatch.setattr(pg, "fetch_df",
                         lambda sql, params=(): pd.DataFrame([{"is_open": None}]))
    monkeypatch.setattr(pg, "quote_trade_date", lambda: "2027-01-04")
    assert pg.trade_day_open("2027-01-04", dt.datetime(2027, 1, 4, 15, 30)) is True


def test_probe_never_leaves_the_test_process():
    """真函数在 pytest 进程里必须直接回 None —— 出站探测的洞只留一个，且留在这层。
    （`conftest.py` 那圈 socket 拦截是第二道，不算这条的证据。）"""
    assert pg.quote_trade_date() is None


def test_calendar_coverage_reports_how_much_is_left(monkeypatch):
    """余量读数：界面与每日链共用，`running_out` 必须是布尔不是 None（否则前端
    的"还剩几天"永远不响）。"""
    monkeypatch.setattr(pg, "calendar_max_day",
                        lambda: (dt.date.today() + dt.timedelta(days=200)).isoformat())
    cov = pg.calendar_coverage()
    assert cov["running_out"] is False and cov["days_left"] >= 199
    monkeypatch.setattr(pg, "calendar_max_day",
                        lambda: (dt.date.today() + dt.timedelta(days=9)).isoformat())
    cov = pg.calendar_coverage()
    assert cov["running_out"] is True and cov["days_left"] <= 9
    monkeypatch.setattr(pg, "calendar_max_day", lambda: None)
    assert pg.calendar_coverage() == {"max_date": None, "days_left": None,
                                      "running_out": False}
