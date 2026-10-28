# -*- coding: utf-8 -*-
"""「几天几板」：规则本身 + 它在短线情绪那块上的接法。

用户 2026-10-09 要的是「三天两板 / 5天4板」这一类分类，并指定回溯前 30 个交易日。
东财池自带的 `zttj` 在 2026-10-08 实测与连板数逐只相等（那天没有一只中间断板），
所以必须本地数 —— 而本地数最容易写错的三件事都在这里钉住：

1. **尾巴不算天数**：一轮开始之前的空日属于上一轮，把它们数进"几天"就会把 4 天说成 5 天；
2. **只空 1 天仍算同一轮**：这正是「5天4板」这个形态本身，写成"必须连续"就永远出不来；
3. **今天没封住就没有这一轮**：交回 None，不退回上一轮的历史值（那是昨天前天的分类）。
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

VR = Path(__file__).resolve().parent.parent / "vr"
if str(VR) not in sys.path:
    # 追加，不插到 0：仓库根有 `app/` 包、vr 里有个 `app.py`，插到 0 会让
    # `from app.compute import …`（duanxian.quant 顶层）拿到 vr/app.py 这个模块而报"不是包"。
    sys.path.append(str(VR))

import firstboard          # noqa: E402  market.py 里是函数内导入，patch 模块本身才盖得住
import market              # noqa: E402  这块榜单在 vr/ 那一层
from duanxian import board_span as bs
from duanxian import limit_up_board


def _days(pairs):
    """[('MM-DD', 封没封)] → 带日期的升序序列（日期只用占位，规则只看真假与顺序）。"""
    base = dt.date(2026, 9, 1)
    return [(base + dt.timedelta(days=i), ok) for i, (_l, ok) in enumerate(pairs)]


# ---------------------------------------------------------------- 规则本身

def test_纯连板_几天就等于几板():
    seq = _days([(None, True)] * 4)
    assert bs.span_from_sealed(seq) == (4, 4, [d for d, _ in seq])


def test_中间空一天仍在同一轮_这就是5天4板():
    seq = _days([(None, True), (None, True), (None, False), (None, True), (None, True)])
    n, m, dates = bs.span_from_sealed(seq)
    assert (n, m) == (5, 4) and len(dates) == 4


def test_轮首之前的空日不算进几天():
    """`空 空 封 封 空 封 封`：这一轮是 5 天 4 板；不把轮首之前那两天剥掉就会数成 7 天 4 板。"""
    seq = _days([(None, False), (None, False), (None, True), (None, True), (None, False), (None, True), (None, True)])
    n, m, dates = bs.span_from_sealed(seq)
    assert (n, m) == (5, 4)
    assert len(dates) == 4, "封板日只列真封住的那几天：跨度 5 天里含一天空窗"


def test_连续两天没封住就断在这一轮结束():
    seq = _days([(None, True), (None, True), (None, False), (None, False), (None, True), (None, True)])
    assert bs.span_from_sealed(seq)[:2] == (2, 2)


def test_今天没封住就没有这一轮_交None而不是历史值():
    seq = _days([(None, True), (None, True), (None, True), (None, False)])
    assert bs.span_from_sealed(seq) is None
    assert bs.span_from_sealed([]) is None


def test_全是空日也不会给一个零板():
    assert bs.span_from_sealed(_days([(None, False)] * 5)) is None


# ---------------------------------------------------------------- 查询写法

def test_按全码等值查而不是对每行套left():
    """实测同一个查询：`left(security_code,6)=any(...)` 2.63 秒、`security_code=any(全码)` 0.25 秒。
    这一列在首屏读，慢十倍就是首屏慢十倍。

    10-27 起这条 SQL 住在 `_sealed_series`（几天几板与近 20 场几板共用一份取数），
    所以判据读的是那一份 —— 并且要求两个读数都走它，谁都不许另写一遍封住的判据。
    """
    import inspect

    src = inspect.getsource(bs._sealed_series)
    assert "sd.security_code = any(" in src, "改回对每行套函数就会退化成全表扫"
    assert "left(sd.security_code, 6) = any(" not in src.split("with win")[1], "对 stock_daily 不许再用 left() 过滤"
    assert "reference_data.security" in src, "6 位码要先展开成全码"
    for fn in (bs.boards_by_code, bs.sealed_days_by_code):
        assert "_sealed_series(" in inspect.getsource(fn), \
            f"{fn.__name__} 绕开了共用取数，封住的容差会在两处各写一遍"


def test_口径文字带齐三个数字_页面直接用它不复述():
    text = bs.RULE_NOTE
    assert str(bs.WINDOW_SESSIONS) in text and str(bs.BREAK_DAYS) in text
    assert "收盘" in text and "涨停价" in text


# ---------------------------------------------------------------- 接法
#
# 2026-10-26：这一块的母体从"现抓东财池"改成读库（`analytics.limit_up_pool_daily`）。
# 桩位跟着从 `astock.em_zt_topic_pool` 换到 `duanxian.limit_up_board.board_on`，
# **三条断言一条没松**：两列说的是两件事、数不出要点名、整列读不到要说读不到。
# 假读数的形状由 `tests/support_limit_up_board.py` 造，与真读侧逐键比对钉在
# `tests/test_limit_up_board_contract.py`（桩与契约分叉要先炸在这里）。

import support_limit_up_board as sup


@pytest.fixture
def board(monkeypatch):
    def _apply(rows, boards):
        monkeypatch.setattr(limit_up_board, "board_on", lambda day: sup.fake_board(rows))
        monkeypatch.setattr(firstboard, "data_session", lambda: (sup.DAY, None))
        monkeypatch.setattr(bs, "boards_by_code", lambda codes, day: boards)
    return _apply


def _row(code, name, consec, src_days, src_ct):
    """落库那一行的形状（`consec` 是本地连续封住场次；源站「几天几板」原文另带一份）。"""
    return sup.landed_row(code, name, consec,
                          src_high=None if src_days is None else f"{src_days}天{src_ct}板")


def test_每一行都带上几天几板与源站那份可以对照(board):
    # 乙：`封 封 空 封 封` ⇒ 本地连续封住只有 2（最后两天连续），这一轮实际是 5 天 4 板 —— 这正是新列要说的话
    board([_row("000001", "甲", 4, 4, 4), _row("000002", "乙", 2, 5, 4)],
          {"000001": {"days": 4, "boards": 4, "label": "4天4板", "sealed_dates": ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"], "rows": 30, "sealed_today": True, "has_today_row": True},
           "000002": {"days": 5, "boards": 4, "label": "5天4板", "sealed_dates": ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"], "rows": 30, "sealed_today": True, "has_today_row": True}})
    rows = {r["name"]: r for r in market._emotion()["lianban_stocks"]}
    assert rows["甲"]["days_boards"] == "4天4板" and rows["乙"]["days_boards"] == "5天4板"
    assert rows["乙"]["board_ct"] == 4 and rows["乙"]["board_days"] == 5
    assert rows["乙"]["boards"] == 2, "连板列仍是本地那份连续场次 —— 两列说的是两件事"
    assert rows["乙"]["src_high_days"] == "5天4板", "源站几天几板也带着，两边对不上时读者看得见"
    assert len(rows["甲"]["board_dates"]) == 4


def test_母体判成封住而board_span数不到就点名而不是显示零板(board):
    board([_row("000003", "丙", 2, 2, 2)],
         {"000003": {"days": None, "boards": None, "label": None, "sealed_dates": [],
                     "rows": 30, "sealed_today": False, "has_today_row": False}})
    out = market._emotion()
    r = out["lianban_stocks"][0]
    assert r["days_boards"] is None
    assert "丙" in out["days_boards_warn"] and "更严的容差" in out["days_boards_warn"]


def test_整列读不到时要说读不到而不是这批票没板(board):
    board([_row("000004", "丁", 2, 2, 2)], {})
    out = market._emotion()
    assert "读不到" in out["days_boards_warn"], out["days_boards_warn"]
    assert out["lianban_stocks"][0]["days_boards"] is None


def test_口径随榜单一起交出前端不自己写规则(board):
    board([_row("000005", "戊", 3, 3, 3)],
         {"000005": {"days": 3, "boards": 3, "label": "3天3板", "sealed_dates": [],
                     "rows": 30, "sealed_today": True, "has_today_row": True}})
    out = market._emotion()
    assert out["days_boards_rule"] == bs.RULE_NOTE
    assert out["days_boards_window"] == bs.WINDOW_SESSIONS
    assert out["days_boards_break_days"] == bs.BREAK_DAYS
