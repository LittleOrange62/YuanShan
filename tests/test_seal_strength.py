# -*- coding: utf-8 -*-
"""「封板强度」：判据本身 + 它在连板榜上的接法。

用户 2026-10-09 两条要求分开钉：
① 同连板按**首封先后**排（早→晚）—— 钉在 `vr/market.py::_emotion()` 的行序上；
② 新增一列**封板强度**（封单占流通市值 / 开板次数 / 首封时间 三项加权），算法要能在页面查看 ——
   判据只住 `duanxian/seal_strength.py`，页面悬停看到的那段说明由同一份档位表现拼，
   所以这里既测分数，也测"说明里写的分档 = 代码里在跑的分档"。
"""
import sys
from pathlib import Path

import pytest

VR = Path(__file__).resolve().parent.parent / "vr"
if str(VR) not in sys.path:
    sys.path.append(str(VR))     # 追加，不插 0（vr 里的 app.py 会顶掉仓库根的 app 包）

import firstboard          # noqa: E402  这屏的连板榜在 vr/ 那一层
import market              # noqa: E402
import support_limit_up_board as sup
from duanxian import limit_up_board
from duanxian import seal_strength as ss


# ---------------------------------------------------------------- 分档本身

@pytest.mark.parametrize('ratio, want', [
    (14.9, 1.00), (3.0, 1.00), (2.9, 0.85), (1.0, 0.85), (0.9, 0.70), (0.5, 0.70),
    (0.4, 0.50), (0.2, 0.50), (0.1, 0.30), (0.05, 0.30), (0.04, 0.15), (0.0, 0.15),
])
def test_封单占比是越大越强并到保底为止(ratio, want):
    assert ss.fund_score(ratio) == want


@pytest.mark.parametrize('opens, want', [
    (0, 1.00), (1, 0.70), (2, 0.50), (3, 0.35), (4, 0.20), (5, 0.20), (6, 0.05), (15, 0.05),
])
def test_开板次数越多越弱(opens, want):
    """「首封之后是否开过板」就是这一条：0 次是从头到尾没开过。"""
    assert ss.reopen_score(opens) == want


@pytest.mark.parametrize('fbt, want', [
    (92500, 1.00), (93000, 0.95), (93022, 0.85), (94000, 0.85), (94115, 0.70),
    (100556, 0.55), (103000, 0.55), (112959, 0.40), (113000, 0.40),
    (113009, 0.25),         # 档是"到 11:30:00 为止"，11:30:09 已经掉到下一档
    (131309, 0.25), (140000, 0.25),
    (145511, 0.10),        # 尾盘才封住
])
def test_首封越早越强(fbt, want):
    assert ss.first_seal_score(fbt) == want


def test_三条权重加起来是一百分():
    assert sum(ss.WEIGHTS.values()) == 100


# ---------------------------------------------------------------- 缺数不当 0

def test_三项都到最强档就是一百分():
    r = ss.seal_strength(1775758428, 11899000000, 0, 92500)      # 封单 14.9% + 没开板 + 一字
    assert r["score"] == 100 and r["missing"] == []


def test_缺一项就在其余项上重新归一而不是按零算():
    no_fund = ss.seal_strength(None, 3185588787, 1, 92500)
    assert no_fund["missing"] == ["fund"]
    # 按 0 算会得到 (35×0.70 + 20×1.00) / 100 = 45 分；归一后应当明显更高
    assert no_fund["score"] == round((35 * 0.70 + 20 * 1.00) / 55 * 100)
    assert no_fund["score"] > 45, "缺封单被当成 0 分参与排名了"


def test_流通市值为零或缺失也算缺项而不是把占比压成零():
    assert ss.seal_strength(1000, 0, 1, 92500)["missing"] == ["fund"]
    assert ss.seal_strength(1000, None, 1, 92500)["missing"] == ["fund"]


def test_只剩一条有数就不给分():
    """归一化会把那一条自己放大成满分 —— 只看首封时间就"强度 100"是造假，不是估分。"""
    only_first = ss.seal_strength(None, None, None, 92500)
    assert only_first["score"] is None
    assert sorted(only_first["missing"]) == ["fund", "reopen"]
    assert only_first["parts"], "分和理由一起交：页面那一格要能说清为什么是 —"


def test_nan进来的也是缺数而不是零():
    r = ss.seal_strength(float("nan"), 3e9, 0, 92500)     # 封单 NaN → 缺，还剩两条
    assert r["missing"] == ["fund"] and r["score"] == round((35 * 1.00 + 20 * 1.00) / 55 * 100)


# ---------------------------------------------------------------- 页面上那段说明

def test_说明里的数字就是代码在跑的那几张表():
    text = ss.spec_text()
    assert ss.SPEC_VERSION in text
    for hi, sc in ss.FUND_STEPS:
        assert "≥%g%% %.2f" % (hi, sc) in text
    for hi, sc in ss.REOPEN_STEPS:
        assert "≤%d 次 %.2f" % (hi, sc) in text
    for hi, sc in ss.FIRST_STEPS:
        assert "%s 及以前 %.2f" % (ss._hhmm(hi), sc) in text
    assert "不按 0 算" in text, "缺数口径必须写在说明里，读者才敢拿这一列比"
    assert "不构成任何交易建议" in text


# ---------------------------------------------------------------- 榜单接法
#
# 2026-10-26：母体改成读库，桩位从 `astock.em_zt_topic_pool` 换到
# `duanxian.limit_up_board.board_on`（形状由 `tests/support_limit_up_board.py` 保证与真读侧一致）。
# 三条钉住的仍是原来那三条：同连板按首封先后、并列用强度收口、缺数排最后且显示空。

def _row(code, name, boards, seal, fund, ltsz, reopen, amount=1000):
    return sup.landed_row(code, name, boards, seal=seal, order=fund, cap=ltsz,
                          reopen=reopen, amount=amount)


@pytest.fixture
def fake_board(monkeypatch):
    """把 `_emotion()` 的输入全按住：库里那一场的整块读数、日期判定。"""
    def _apply(rows):
        monkeypatch.setattr(limit_up_board, "board_on",
                            lambda day: sup.fake_board(rows))
        monkeypatch.setattr(firstboard, "data_session", lambda: (sup.DAY, None))
    return _apply


def test_同连板按首封先后而不是按成交额顶大票(fake_board):
    """这里原来是 `(-连板, -成交额)`：同板数里成交额大的排前面，
    回答不了用户要的那一句"同样连板数量按首封时间先后排"。"""
    fake_board([
        _row("000001", "晚封大盘", 3, 140000, 5e7, 1e10, 0, amount=9e8),
        _row("000002", "早封小票", 3, 92500, 5e7, 1e10, 0, amount=1e7),
        _row("000003", "中间封", 3, 93506, 5e7, 1e10, 0, amount=5e8),
        _row("000004", "最高板", 5, 100000, 5e7, 1e10, 0),
    ])
    rows = market._emotion()["lianban_stocks"]
    assert [r["name"] for r in rows] == ["最高板", "早封小票", "中间封", "晚封大盘"]
    assert [r["boards"] for r in rows] == [5, 3, 3, 3]
    assert [r["seal_time"] for r in rows[1:]] == ["09:25", "09:35", "14:00"]


def test_同一分钟并列按秒级先后再用强度收口(fake_board):
    """集合竞价那批经常同一分钟并列（显示只到分钟，库里存到秒）——
    先按秒级先后，真同一秒才轮到强度分，同一场里刷新两次行序不会漂。"""
    fake_board([_row("000001", "弱一点的", 3, 92500, 1e7, 1e9, 1),
                _row("000002", "硬一点的", 3, 92500, 9e7, 1e9, 0),
                _row("000003", "晚一点的", 3, 92530, 9e7, 1e9, 0)])
    rows = market._emotion()["lianban_stocks"]
    assert [r["name"] for r in rows] == ["硬一点的", "弱一点的", "晚一点的"], \
        "首封秒数优先于强度：09:25:30 的硬板排在 09:25:00 之后"
    assert [r["seal_score"] for r in rows] == [100, 83, 99], \
        "92530 已经出了「集合竞价一字」那一档（09:25:00→1.00，09:30:00 前→0.95），所以第三行是 99 不是 100"


def test_首封缺数排到本档最后并显示成空(fake_board):
    """`seal=None` 是"这一场判不出首封"，既不能顶到最前，也不能显示成 00:00。"""
    fake_board([_row("000001", "有首封", 2, 93000, 1e7, 1e9, 0),
                _row("000002", "没给首封", 2, None, 1e7, 1e9, 0)])
    rows = market._emotion()["lianban_stocks"]
    assert [r["name"] for r in rows] == ["有首封", "没给首封"]
    assert rows[1]["seal_time"] == "" and rows[1]["seal_raw"] is None
    assert rows[1]["seal_basis"] == "none", "两把尺都没有 ⇒ 行上要标出来，不能混进自算那一族"


def test_每行带强度分与这一行为这个分的原因(fake_board):
    fake_board([_row("000001", "硬板", 2, 92500, 1775758428, 11899000000, 0),
                _row("000002", "软板", 2, 131309, 4550000, 43750724590, 8)])
    hard, soft = market._emotion()["lianban_stocks"]
    assert hard["seal_score"] == 100
    assert soft["seal_score"] < 30
    assert "开板 8 次" in soft["seal_note"] and "首封 13:13" in soft["seal_note"]
    assert soft["open_times"] == 8


def test_退回源站首封要在悬停里说出来(fake_board):
    """这一场没有分钟线（10-20/10-21 那种）时那一格取的是源站秒级值 ——
    同一个列名底下混两把尺而不标注，是这一屏最容易糊过去的一件事。"""
    fake_board([sup.landed_row("000001", "无分钟线", 2, seal=None, src_seal=92530,
                               order=1e7, cap=1e9, reopen=0)])
    row = market._emotion()["lianban_stocks"][0]
    assert row["seal_basis"] == "source" and row["seal_time"] == ""
    assert row["src_seal_time"] == "09:25", "源站那一份留着，悬停里说得出差在哪"


def test_算法说明随榜单一起交出来并带着口径版本(fake_board):
    fake_board([_row("000001", "硬板", 2, 92500, 1e7, 1e9, 0)])
    out = market._emotion()
    assert out["seal_spec_version"] == ss.SPEC_VERSION
    assert out["seal_spec"] == ss.spec_text(), "页面那段说明必须是判据那一份现拼的"
