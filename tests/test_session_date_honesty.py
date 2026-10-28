""""显示哪一天"这条规则只许有一处答案：① 日历 → ② 库里实际，两头的顺序不能反。

⚠️ 2026-09-26 用户当场抓到首板页把中秋休市日 09-25 当成了交易日。根因不是日历算错，
是页面压根不过日历：`_emotion()` 与首板都写成"从今天往前 8 天，第一个非空的池子算谁"。
而实测东财涨停池对 09-23/09-24/09-25（休市）/09-26（周六）四天**都回 51~52 家、
首封时间一律 09:25** —— 它不校验日期有效性，"非空"因此完全不能当"这天有数"用。
所以本文件锁三件事：非交易日不请求、请求的日子必须是日历+库里判定出来的那一个、
判定结果与库里对不上时必须在返回值里说出来（不静默取一边）。

2026-10-26 短线情绪那一块的母体落进库里（`analytics.limit_up_pool_daily`）之后，
这三件事变成：**日历判哪天、库判有没有数、两侧口径的实测差随榜单交出**——
原来那三条意图一条没删，只是把"请求了哪个日期"换成"查了哪一场"。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

VR = Path(__file__).resolve().parent.parent / "vr"
if str(VR) not in sys.path:
    sys.path.append(str(VR))     # 追加，不插 0（vr 里的 app.py 会顶掉仓库根的 app 包）

import firstboard  # noqa: E402
import market  # noqa: E402


def _resolved(monkeypatch, date="2026-09-23", note=None):
    """拦住"①日历 + ②库里"那两层判定（2026-10-26 之后这一屏不再有出网调用，
    原来的 `get_reasons` / `zt_count_from_db` 两处桩已经跟着母体搬家一起删掉）。"""
    monkeypatch.setattr(firstboard, "data_session", lambda: (date, note))


def _cal_and_db(monkeypatch, *, closed, have):
    """把判定的两层换掉：① `latest_session()`（日历）② `pg.latest_session()`（库里实际）。"""
    from duanxian import pg, trade_calendar

    monkeypatch.setattr(trade_calendar, "latest_session", lambda: closed)
    monkeypatch.setattr(pg, "latest_session", lambda table: have)


# ---------- ①+② 判定：日历晚于库里时，显示库里那天并把缺的那场讲出来 ----------

def test_calendar_ahead_of_db_shows_db_date_and_says_why(monkeypatch):
    from duanxian import trade_calendar

    _cal_and_db(monkeypatch, closed="2026-09-24", have="2026-09-23")
    got, note = trade_calendar.latest_data_session()
    assert got == "2026-09-23", "库里没有 09-24 的数，就不能显示 09-24"
    assert "2026-09-24" in note and "2026-09-23" in note


def test_calendar_and_db_agree_is_silent(monkeypatch):
    from duanxian import trade_calendar

    _cal_and_db(monkeypatch, closed="2026-09-23", have="2026-09-23")
    assert trade_calendar.latest_data_session() == ("2026-09-23", None)


def test_empty_db_never_returns_a_fake_date(monkeypatch):
    from duanxian import trade_calendar

    _cal_and_db(monkeypatch, closed="2026-09-24", have=None)
    got, note = trade_calendar.latest_data_session()
    assert got is None and note


def test_review_pages_share_one_date_rule(monkeypatch):
    """复盘页 expected-date 与首板必须走同一个判定，不能一个说日历、一个说库里。"""
    _cal_and_db(monkeypatch, closed="2026-09-24", have="2026-09-23")
    got, note = firstboard.data_session()
    assert got == "2026-09-23" and "2026-09-24" in note

    server_py = Path(__file__).resolve().parents[1] / "server.py"
    src = server_py.read_text(encoding="utf-8")
    at = src.index("def api_review_expected_date")
    body = src[at:at + 600]
    assert "latest_data_session()" in body, "复盘页默认日期必须走 ①日历+②库里 的判定"
    assert "latest_session()" not in body, "只回日历那一层会把用户指向库里没有数的一天"


# ---------- ③ 母体只按判定出来的那一场读库，非交易日一次都不许碰 ----------
#
# 2026-10-26：这一块的母体从"现抓东财池"改成读 `analytics.limit_up_pool_daily`。
# 原来钉的四条意图一条没松，只是把"请求了哪个日期"换成"查了哪一场"，
# 把"在线池与库里对不上要出声"换成"两把尺的实测差与首封出处要出声"。

import support_limit_up_board as sup
from duanxian import board_span as bs
from duanxian import limit_up_board


def _read_board(monkeypatch, rows, *, day="2026-09-23"):
    """拦住读侧，返回"实际查过的那一场"列表。"""
    seen: list[str] = []

    def fake(date):
        seen.append(date)
        return sup.fake_board(rows, day=date)

    monkeypatch.setattr(limit_up_board, "board_on", fake)
    monkeypatch.setattr(bs, "boards_by_code",
                        lambda codes, day: {c: {"days": 2, "boards": 2, "label": "2天2板",
                                                "sealed_dates": ["2026-09-22", "2026-09-23"],
                                                "rows": 30, "sealed_today": True,
                                                "has_today_row": True} for c in codes})
    return seen


def _row(code="600000", consec=2, seal=93000):
    return sup.landed_row(code, "测试股", consec, seal=seal, order=1e7, cap=1e9, reopen=0)


def test_emotion_requests_only_the_resolved_session(monkeypatch):
    _resolved(monkeypatch)
    seen = _read_board(monkeypatch, [_row()])
    out = market._emotion()
    assert seen and set(seen) == {"2026-09-23"}, f"查了别的场次：{set(seen)}"
    assert out["date"] == "2026-09-23"


def test_emotion_never_guesses_by_going_backwards(monkeypatch):
    """把"今天往前回溯、第一个非空算谁"这个写法本身禁掉 —— 母体进了库，回溯同样会挑出旧批次。"""
    src = inspect.getsource(market._emotion)
    assert "for back in range" not in src, "不得再用往前回溯定位交易日"
    assert "data_session" in src, "必须复用 firstboard 的 ①日历+②库里 判定"
    assert "em_zt_topic_pool" not in src, "母体已落库，收盘快照这一不许再回退去抓在线池子顶数"


def test_pool_failure_yields_empty_not_other_days_numbers(monkeypatch):
    """库里没有这一场的行 ⇒ 老实说"这一场没有行、该跑哪一步"，**不许拿别的日子顶上**。"""
    _resolved(monkeypatch)
    _read_board(monkeypatch, [])
    monkeypatch.setattr(limit_up_board, "board_on",
                        lambda day: sup.fake_board([], unavailable="这一场还没加工"))
    out = market._emotion()
    assert out["available"] is False and out["date"] == "2026-09-23"
    assert out["lianban_stocks"] == [], "读不到就是读不到，不许造一行出来"
    assert "还没加工" in out["reason"], "要说出该跑哪一步，不能只让页面显示空白"


def test_gap_note_reaches_the_page_payload(monkeypatch):
    _resolved(monkeypatch, note="2026-09-24 已收盘但库里没有那一场的日线")
    _read_board(monkeypatch, [_row()])
    assert "2026-09-24" in market._emotion()["caliber_note"]


def test_caliber_divergence_is_stated_not_hidden(monkeypatch):
    """判据从东财池换成本地日线：两侧的实测差必须随榜单交出，不许静默换成另一套数。"""
    _resolved(monkeypatch)
    _read_board(monkeypatch, [_row(code=f"6000{i:02d}") for i in range(51)])
    out = market._emotion()
    assert out["zt_count"] == 51 and out["spec_version"] == "lup-1"
    assert "84 对 82" in out["caliber"], "口径文字里必须带着两侧实测数，读者才能自己判断差多少"
    assert "北交所" in out["caliber"] and "ST" in out["caliber"], "差在哪一族的来处要点名"


def test_seal_basis_is_named_in_the_note(monkeypatch):
    """整场退回源站首封（没有分钟线那几天）要在 caliber_note 里出声，
    同一个列名底下混两把尺而不标注，界面读起来就是自算口径。"""
    _resolved(monkeypatch)
    _read_board(monkeypatch, [sup.landed_row("600001", "无分钟线", 2, seal=None,
                                             src_seal=92530, order=1e7, cap=1e9, reopen=0)])
    out = market._emotion()
    assert "源站秒级值" in out["caliber_note"]
    assert out["lianban_stocks"][0]["seal_basis"] == "source"
