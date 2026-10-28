# -*- coding: utf-8 -*-
"""通达信看护的"该在而不在"：夜里客户端停着不该记成连败（任务 #85）。

10-08 22:44 实测 `scripts/jobs.py status` 上 `tdx_watch` 是「上次失败 22:44（连续 11 次）」，
而那句理由其实是**状态报告**（"通达信没在跑"）不是故障 —— 链跑完就没有任何理由让客户端开着，
"收工后自动关"那一档还默认关着。天天红 = 真故障混在里面看不出来。

规矩：只有**该开着的时段**读到"没在跑"才算失败，其余记成 `skipped`
（`jobs.record` 的第三态：不动 `consecutive_failures`、不刷 `last_ok_at`，原因照旧写出来）。
"""
import datetime as dt
import inspect

import pytest

import server
from duanxian import jobs as jstore
from duanxian import pg, util


@pytest.fixture
def clock(monkeypatch):
    """钉住"今天哪天几点、开不开市、链几点跑、提前几分钟拉起"。

    ⚠️ `china_today` 的桩**必须交字符串**：生产里 `util.china_today()` 返回 `'YYYY-MM-DD'` 的 str，
    而这一度被打成 `dt.date` —— 于是被测代码写 `.isoformat()` 也测不出来，
    10-09 21:37 之后看护循环每两分钟在记账前抛 `AttributeError`（被 except 吞成一行 stdout），
    台账冻住 18 小时、界面上仍是"正常·连败 0"。**桩要按真契约，别按想象的类型。**
    """
    def _set(hhmm: str, *, opened=True, lead=0, slot="15:59"):
        day = dt.date(2026, 10, 9)
        hh, mm = [int(x) for x in hhmm.split(":")]
        monkeypatch.setattr(util, "china_today", lambda: day.isoformat())
        monkeypatch.setattr(util, "china_now",
                            lambda: dt.datetime.combine(day, dt.time(hh, mm)))
        monkeypatch.setattr(pg, "trade_day_open", lambda d, **k: opened)
        monkeypatch.setattr(jstore, "run_at", lambda *a, **k: (slot, "测试钉住"))
        monkeypatch.setattr(jstore, "settings",
                            lambda name, defaults=None, **k: ({"launch_lead_min": lead}, []))
    return _set


# ----------------------------------------------------- 真契约（不打桩的那一层）

def test_china_today交的是字符串():
    """这条是桩的护栏：谁哪天把 `china_today()` 改成 date，这里先红，而不是让看护循环炸在夜里。"""
    assert isinstance(util.china_today(), str) and len(util.china_today()) == 10


def test_不打桩跑一遍该不该在_不许抛():
    """10-10 那 18 小时假绿的唯一现场复现：**用真时钟、真类型**跑一次这道闸。
    打桩的用例可以一起骗人（桩给 date、被测代码要 isoformat），这条没有桩可骗。"""
    expected, why = server._tdx_expected_now()
    assert isinstance(expected, bool)
    assert isinstance(why, str) and why, "这条闸每次否决都要说得出为什么"


def test_链前后那段窗口里没在跑才算该在(clock):
    clock("16:20")
    assert server._tdx_expected_now()[0] is True


def test_夜里这个时段本来就不该有它(clock):
    clock("22:44")
    expected, why = server._tdx_expected_now()
    assert expected is False
    assert "时间带" in why and "15:49" in why, why


def test_不开市的那天整天都不该在(clock):
    clock("16:20", opened=False)
    expected, why = server._tdx_expected_now()
    assert expected is False and "不开市" in why


def test_窗口下界跟着提前拉起那一档走(clock):
    """`launch_lead_min=20` → 下界从 15:49 挪到 15:29，于是 15:35 就成了"该在"。"""
    clock("15:35", lead=0)
    assert server._tdx_expected_now()[0] is False
    clock("15:35", lead=20)
    assert server._tdx_expected_now()[0] is True


def test_链窗口一过就交回给人(clock):
    """180 与宿主那条 `_plus_minutes(slot, 180)` 是同一个数：过了就不是看护的事。"""
    clock("19:00")
    expected, why = server._tdx_expected_now()
    assert expected is False and "18:59" in why, why


def test_日历判不了时按照跑处理(clock):
    """`trade_day_open=None`（日历没这一行又是工作日）时链自己会照跑 ⇒ 客户端也算该在。"""
    clock("16:20", opened=None)
    assert server._tdx_expected_now()[0] is True


# ------------------------------------------------------------------ 记账那一层

def test_只有确实读到没在跑才降级登录框和探测坏了仍算失败():
    """源码级钉：这道闸若写成"所有 not ok 都跳过"，就会把"开着但停在登录框上"
    （TQ 那条腿照样取不到数 = 真故障）一起洗成没事。"""
    body = inspect.getsource(server._tdx_watch_scheduler)
    assert 'detail.get("running") is False' in body, "降级只许落在「读到了没在跑」这一种读数上"
    assert "skipped=True" in body and "_tdx_expected_now()" in body


def test_这道闸坏了不许带停整轮_记账收进程阶梯都在它后面():
    """10-10 的教训：抛点在**记账、收同步进程、收空壳、下载阶梯之前**，一句 `AttributeError`
    就把四件事一起冻了 18 小时。所以这道闸必须自己兜住（兜不住=按"该在"出声，不静默）。"""
    body = inspect.getsource(server._tdx_watch_scheduler)
    gate = body.index("_tdx_expected_now()")
    tail = body[gate:]
    assert "try:" in body[max(0, gate - 400):gate], "这道闸必须包在自己的 try 里"
    assert "gate_error" in tail.split("jobs.record")[0], "闸坏了要留痕，不许静默当没事"
    for later in ("_reap_stale_sync()", "_reap_plugin_shells()", "_download_ladder_round()",
                  'jobs.record("tdx_watch"'):
        assert body.index(later) > gate, later + " 排在闸之后 —— 闸一抛它就整轮不跑了"


def test_skipped那一笔不动连败计数():
    """`jobs.record` 的第三态：跳过 ≠ 失败（连败不涨）≠ 成功（不刷 last_ok_at）。"""
    jstore.record("tdx_watch", ok=False, detail={"reason": "第一笔真失败"})
    jstore.record("tdx_watch", ok=False, detail={"skipped": True, "reason": "夜里不该有它"})
    st = jstore.state()["tdx_watch"]
    assert st["consecutive_failures"] == 1, st
    assert st["last_status"] == "skipped" and st["last_ok_at"] is None
    assert "夜里不该有它" in st["last_error"]
