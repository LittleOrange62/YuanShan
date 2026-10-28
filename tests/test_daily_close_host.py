"""15:59 收盘链宿主（`server.py` 第 6 条常驻循环）的回归。

钉的是五条"错了会安静毁掉一天数据"的行为：
段1 没真跑成不许起段2、判不了交易日不许当非交易日、非交易日不跑、
只有真成功才放行、**每条链自己的退出码按它自己的文档读**（两条链的 3 不是一回事）。
"""
from __future__ import annotations

import datetime as dt
import pathlib
import subprocess

import pytest


class _Done(Exception):
    """让 while True 只走一轮。"""


def _fake_proc(rc=0, out="ok"):
    return subprocess.CompletedProcess([], rc, out, "")


@pytest.fixture
def one_round(monkeypatch):
    """跑 `_daily_close_scheduler` 的循环体 N 轮（一轮 = 一次 60 秒心跳），
    返回（被调起的命令, 记录的运行结果）。宿主的今日计数活在函数体内，
    所以要验"重试上限"必须一次跑多轮，不能靠反复调用本 fixture。"""
    import server
    calls: list[list[str]] = []
    recorded: list[tuple[str, bool, dict]] = []
    scripted = {"stage1": 0, "stage2": 0, "timeout": False}

    def make_sleep(left: list[int]):
        def _sleep(_s):
            left[0] -= 1
            tick['n'] += 1          # 一轮心跳 = 一分钟：重试节奏表按分钟判"到档了没"
            if left[0] <= 0:
                raise _Done()
        return _sleep

    tick = {'n': 0}
    monkeypatch.setattr(server, "_daily_close_slot", lambda: "15:59")

    import duanxian.util as util
    monkeypatch.setattr(util, "china_today", lambda: dt.date(2026, 9, 28))
    # ⚠️ 时钟必须跟着心跳走。把它钉死在 15:59 的话，`DAILY_CLOSE_RETRY_MIN` 里
    # 除第一档以外永远"没到点"，于是"重试上限"那几条测试会因为**等不到**而通过，
    # 而不是因为上限真的存在而通过 —— 那是假绿。
    monkeypatch.setattr(util, "china_now",
                        lambda: dt.datetime(2026, 9, 28, 15, 59) + dt.timedelta(minutes=tick['n']))
    import duanxian.jobs as jstore

    def rec(name, *, ok, detail=None):
        recorded.append((name, ok, detail or {}))

    monkeypatch.setattr(jstore, "record", rec)

    # ⚠️ 打的是 `jobs._spawn_stage` 这个洞，**不是 `subprocess.run`**。
    # 2026-10-06 踩过一次：执行体从 `subprocess.run` 改成 `Popen` 之后，这里假的那个
    # `subprocess.run` 再也不被调用，于是这套"宿主循环"测试真的起了 `daily_close_sync`
    # → 再往下 `p0_tdx_predownload` → **在用户桌面上把通达信启动了**。测试改内部实现
    # 不该有权限碰桌面；`_spawn_stage` 之外还有一道 pytest 硬挡（两层都要在）。
    def spawner(module, timeout, argv=()):
        calls.append([module, *argv])
        if scripted["timeout"]:
            return 124, f"超过 {timeout} 秒未完成 → 已按进程树收掉"
        # 按**模块名**取脚本化退出码：重试时同一个段会被敲好几次，
        # 用 len(calls) 判段会把"段1 的第二次尝试"误当成"段2"。
        # 值可以是 callable(分钟数) —— 用来演"上游晚到、到某一档才终于有数"那一天。
        def rc_for(key):
            value = scripted[key]
            return value(tick['n']) if callable(value) else value
        return (rc_for("stage2" if module == "ingest.jobs.e1_fill_all" else "stage1"), "ok")

    monkeypatch.setattr(jstore, "_spawn_stage", spawner)

    def go(stage1_rc=0, stage2_rc=0, trade=True, timeout=False, rounds=1,
           lead=0, only_trade=True, enabled=True, start="15:59"):
        calls.clear()
        recorded.clear()
        scripted.update(stage1=stage1_rc, stage2=stage2_rc, timeout=timeout)
        monkeypatch.setattr(server.time, "sleep", make_sleep([rounds]))
        monkeypatch.setattr(server, "_trade_day_or_none", lambda _d: trade)
        # 时钟可以从更早的分钟开始（演"提前拉起"那一天必须比 15:59 早），一轮心跳 = 一分钟
        h, m = int(start[:2]), int(start[3:5])
        monkeypatch.setattr(util, "china_now",
                            lambda: dt.datetime(2026, 9, 28, h, m) + dt.timedelta(minutes=tick['n']))
        # 宿主每轮都重读作业配置（改开关不必重启），所以测试必须自己给它一份，
        # 否则就是在读真 ~/.duanxian-agents/jobs/config.json —— 绿灯与代码无关。
        def fake_settings(name, defaults, **kw):
            if name != "daily_close":
                return dict(defaults), []
            return dict(defaults, enabled=enabled, launch_lead_min=lead,
                         trading_days_only=only_trade), []
        monkeypatch.setattr(jstore, "settings", fake_settings)
        with pytest.raises(_Done):
            server._daily_close_scheduler()
        return calls, recorded

    return go


def test_宿主已接进常驻进程(one_round):
    """接线与执行体两头都要在：只查 server.py 里有没有那句模块名，
    改过一次"把表挪进数据层"的重构就会假绿 —— 名字在另一个文件里，测试照样过。"""
    import server
    root = pathlib.Path(__file__).resolve().parents[1]
    body = root.joinpath("server.py").read_text(encoding="utf-8")
    assert "_daily_close_scheduler" in body and "_start_daily_close()" in body
    from duanxian import jobs as jstore
    assert list(jstore.CLOSE_CHAIN) == ["daily_close", "fill_all"]
    for key, spec in jstore.CLOSE_CHAIN.items():
        mod = spec["module"].replace(".", "/")
        assert root.joinpath(f"{mod}.py").is_file(), f"{key} 的执行体不存在：{spec['module']}"
    # 宿主真的会起这两个模块（不是只登记在册）
    calls, _ = one_round()
    assert [c[-1] for c in calls] == [jstore.CLOSE_CHAIN[k]["module"]
                                      for k in jstore.CLOSE_CHAIN]


def test_两段都跑且获取段在前(one_round):
    calls, recorded = one_round(stage1_rc=0)
    mods = [c[-1] for c in calls]
    assert mods == ["ingest.jobs.daily_close_sync", "ingest.jobs.e1_fill_all"]
    assert [(n, ok) for n, ok, _ in recorded] == [("daily_close", True), ("fill_all", True)]


def test_获取段失败就不起加工段(one_round):
    """加工跑在缺数的库上会产出一堆"看着算完其实是空"的结果，比失败更难发现。"""
    calls, recorded = one_round(stage1_rc=1)
    assert [c[-1] for c in calls] == ["ingest.jobs.daily_close_sync"]
    assert [(n, ok) for n, ok, _ in recorded] == [("daily_close", False)]


def test_非交易日一子进程都不起(one_round):
    calls, recorded = one_round(trade=False)
    assert calls == [] and recorded == []


def test_交易日判不了时不动手(one_round):
    """`None` 是"读不到日历"，绝不能被当成"今天不跑"—— 那样整条链会安静地天天不跑。"""
    calls, _ = one_round(trade=None)
    assert calls == []


def test_段1的3是要人来不是成功(one_round):
    """`daily_close_sync` 的 3 = 数据没到位且自动手段用尽（它自己的退出码文档就这么写的）。

    必须同时成立：记**不成**（页面上得看见）、段2 不起也不留记录（库里没数，
    而且**没跑过的段不许有时间戳**——契约 03 §3 要求 `last_run_at` 为 null）、
    今天不再重敲（重敲等于把人工下载反复打断）。
    "不再重试"与"算成了"是两件事，这条就是钉住它们不许合成一个 `rc in (0, 3)`。
    """
    calls, recorded = one_round(stage1_rc=3)
    assert [n for n, _, _ in recorded] == ["daily_close"]
    assert recorded[0][1] is False
    assert [c[-1] for c in calls] == ["ingest.jobs.daily_close_sync"]
    assert recorded[0][2]["verdict"] == "stop"
    assert "要人来" in recorded[0][2]["reason"]      # record() 只认 reason 这个键名


def test_段1的2是按设计跳过(one_round):
    """段1 自己判定"未到收盘线/非交易日"→ 不记失败，但同样不许放行段2。"""
    calls, recorded = one_round(stage1_rc=2)
    assert [(n, ok) for n, ok, _ in recorded] == [("daily_close", True)]
    assert len(calls) == 1
    assert calls[0][-1] == "ingest.jobs.daily_close_sync"
    assert recorded[0][2]["verdict"] == "stop"


def test_作业开关为关只跳过那一段(one_round):
    """关掉获取段（系统配置里的开关）**不等于**连加工也不许跑：
    `e1_fill_all` 自己带 `tdxdl`/`daily` 两步补齐，不硬依赖段1。
    把"人关了一个"读成"整条链都别动"是替操作员做决定。"""
    calls, recorded = one_round(enabled=False)
    assert [c[-1] for c in calls] == ["ingest.jobs.e1_fill_all"]
    # 被跳过的一段**不跑但要说清为什么**：页面上"已停用"分不出是用户不想跑还是被谁误关
    assert [n for n, _, _ in recorded] == ["daily_close", "fill_all"]
    assert recorded[0][1] is False and recorded[0][2]["verdict"] == "disabled"
    assert "开关为关" in recorded[0][2]["reason"]


def test_重试耗尽也不许越过它去跑下一段(one_round):
    """这条是整段里最容易写错的一处：失败到上限后"这段不用再管了"，
    很容易写成 `continue` 去试下一段 —— 于是缺数的库照样被加工，
    还披着一身"今天已经算完"的绿。"""
    import server
    calls, recorded = one_round(stage1_rc=1, rounds=170)
    assert [c[-1] for c in calls] == ["ingest.jobs.daily_close_sync"] * server.DAILY_CLOSE_MAX_TRIES
    assert all("e1_fill_all" not in c[-1] for c in calls)


def test_段2的3是骨干好辅助抖(one_round):
    """`e1_fill_all` 的 3 与段1 的 3 **语义相反**：日线是好的，只有两融/龙虎榜抖了。

    这一条与上一条放在一起才有意义 —— 共用一个 `rc in (0, 3)` 必错一边。
    """
    calls, recorded = one_round(stage1_rc=0, stage2_rc=3)
    assert len(calls) == 2
    assert [(n, ok, d["verdict"]) for n, ok, d in recorded] == [
        ("daily_close", True, "run"), ("fill_all", True, "run")]


def test_宿主超时记124且不按设计跳过(one_round):
    """124 是宿主自己掐的表，不在任何一条链的退出码语义里，不许被读成"跳过"。"""
    calls, recorded = one_round(timeout=True)
    assert len(calls) == 1 and len(recorded) == 1      # 敲了一次、超时，段2 不被起
    assert recorded[0][1] is False
    assert recorded[0][2]["rc"] == 124
    assert recorded[0][2]["verdict"] == "hold"


def test_hold有上限不会把一天敲满(one_round):
    """秒回的失败（日历空了 rc=1 就是这样）如果每分钟重敲，三小时窗口要跑 180 次。"""
    import server
    calls, recorded = one_round(stage1_rc=1, rounds=170)
    assert len(recorded) == server.DAILY_CLOSE_MAX_TRIES
    assert all(ok is False for _, ok, _ in recorded)
    assert [d["attempt"] for _, _, d in recorded] == list(range(1, len(recorded) + 1))
    assert 1 <= server.DAILY_CLOSE_MAX_TRIES <= 5


def test_段1跑成后段2的失败独立重试(one_round):
    """段1 已经 done，段2 hold 时不能因为"今天有记录了"就再也不试段2 ——
    原来那句 `if not done` 就是这个洞：段1 成功后段2 一次都不给补。"""
    import server
    calls, recorded = one_round(stage1_rc=0, stage2_rc=1, rounds=170)
    s1 = [c for c in calls if c[-1] == "ingest.jobs.daily_close_sync"]
    s2 = [c for c in calls if c[-1] == "ingest.jobs.e1_fill_all"]
    assert len(s1) == 1                       # 段1 不重跑（它会动通达信窗口）
    assert len(s2) == server.DAILY_CLOSE_MAX_TRIES


def test_上游晚到会被等到而不是被判成没有(one_round):
    """**2026-09-28 那天真正坏掉的地方**：通达信的盘后文件 17:22 才落盘，
    而链 16:31 就烧完了三次尝试（老节奏是"每 60 秒一次、上限 3"）判 stop，
    此后今天再没人回来 —— 于是"晚到一天"等于"缺这一天"。

    这里演的是同一天：段1 每一档都回 4（数据还没到），到第 100 分钟（≈17:39）才回 0。
    断言的是"第四次真的发生了、而且是在 17:2x 之后"，以及数到了以后段2 照常放行。
    """
    from duanxian import jobs as jstore
    calls, recorded = one_round(stage1_rc=lambda n: 0 if n >= 100 else 4, rounds=170)
    assert len(calls) == 5 and len(recorded) == 5
    assert [c[-1] for c in calls] == ["ingest.jobs.daily_close_sync"] * 4 \
        + ["ingest.jobs.e1_fill_all"]
    assert [d["verdict"] for _n, _ok, d in recorded] == ["hold", "hold", "hold", "run", "run"]
    assert [d["rc"] for _n, _ok, d in recorded[:3]] == [4, 4, 4]
    assert recorded[3][:2] == ("daily_close", True)
    assert recorded[4][0] == "fill_all", '数到了还不放行加工，等于白等'
    # 前三次的记账必须是"还没到"，不能是"要人来"（措辞不同，处置完全不同）
    assert all("晚些时候还会自动再看一次" in d["reason"] for _n, ok, d in recorded[:3]
               if not ok)
    assert jstore.DAILY_CLOSE_RETRY_MIN[3] == 100      # 第四次落在 17:39，晚于实测的 17:2x


def test_重试节奏表只有一份实现():
    """宿主的"到档了没"和页面的"今天还会不会再跑"必须给同一个答案。"""
    from duanxian import jobs as jstore
    assert jstore.close_attempts_max() == len(jstore.DAILY_CLOSE_RETRY_MIN)
    assert jstore.close_attempt_due(1, 0) and not jstore.close_attempt_due(1, -1)
    assert not jstore.close_attempt_due(2, 24) and jstore.close_attempt_due(2, 25)
    assert not jstore.close_attempt_due(0, 999)
    assert not jstore.close_attempt_due(jstore.close_attempts_max() + 1, 9999)
    assert jstore.close_next_attempt_min(0) == jstore.DAILY_CLOSE_RETRY_MIN[1]
    assert jstore.close_next_attempt_min(jstore.DAILY_CLOSE_RETRY_MIN[-1]) is None
    # 最后一档必须仍在宿主的 3 小时窗口内，否则"等到"是句空话
    assert jstore.DAILY_CLOSE_RETRY_MIN[-1] <= 180
    # 允许动桌面那个阈值本身必须是一档：否则"到点了就该去点窗口"这句话没人执行
    assert jstore.DAILY_CLOSE_GUI_AFTER_MIN in jstore.DAILY_CLOSE_RETRY_MIN


def test_退出码4是等不是要人来():
    import server
    assert server._daily_close_verdict("daily_close", 4) == ("hold", False)
    assert server._daily_close_verdict("daily_close", 3) == ("stop", False)
    from duanxian import jobs as jstore
    assert "要人来" not in jstore.CLOSE_STOP_TEXT[("daily_close", 4)]
    assert "还会自动再看一次" in jstore.CLOSE_STOP_TEXT[("daily_close", 4)]


def test_退出码表外非零一律hold():
    import server
    assert server._daily_close_verdict("daily_close", 9) == ("hold", False)
    assert server._daily_close_verdict("fill_all", 9) == ("hold", False)
    assert server._daily_close_verdict("fill_all", 1) == ("hold", False)
    assert server._daily_close_verdict("daily_close", 0) == ("run", True)


def test_判不了交易日必须返回_none(monkeypatch):
    """`None` 与 `False` 是两件事：False 才能安静跳过，None 必须出声。

    这里特意不复用 `app.db.repo.schedule.is_trade_day`，因为它按设计把"查不到那一天"
    也返回 False（对它的调用方是对的）。宿主如果照抄，**2027 年日历没同步进来那天起
    这条链就永远显示"非交易日"**，页面上一切正常 —— 所以判据必须自己区分"有没有这一行"。
    """
    import server
    from duanxian import pg as _pg
    import pandas as pd

    monkeypatch.setattr(_pg, "enabled", lambda *a, **k: True)
    monkeypatch.setattr(_pg, "fetch_df",
                        lambda sql, params=(), *a, **k: pd.DataFrame({"is_open": [True]}))
    assert server._trade_day_or_none(dt.date(2026, 9, 28)) is True
    monkeypatch.setattr(_pg, "fetch_df",
                        lambda sql, params=(), *a, **k: pd.DataFrame({"is_open": [False]}))
    assert server._trade_day_or_none(dt.date(2026, 10, 1)) is False
    # 日历里根本没有这一天（没连库、查询失败、行缺失都算）
    monkeypatch.setattr(_pg, "fetch_df", lambda sql, params=(), *a, **k: None)
    assert server._trade_day_or_none(dt.date(2027, 1, 4)) is None
    monkeypatch.setattr(_pg, "fetch_df", lambda sql, params=(), *a, **k: pd.DataFrame())
    assert server._trade_day_or_none(dt.date(2027, 1, 4)) is None
    monkeypatch.setattr(_pg, "fetch_df",
                        lambda sql, params=(), *a, **k: pd.DataFrame({"is_open": [None]}))
    assert server._trade_day_or_none(dt.date(2027, 1, 4)) is None     # 行在但值是 NULL
    monkeypatch.setattr(_pg, "enabled", lambda *a, **k: False)
    assert server._trade_day_or_none(dt.date(2026, 9, 28)) is None    # 库没连


def test_日历判不了时要把原因记进作业(one_round, monkeypatch):
    """只 print 等于没说：stdout 进文件是块缓冲、页面也读不到。

    必须往作业状态记一次（一天一次，不许每 60 秒刷），`reason` 里写清
    "不是今天不是交易日，是不敢猜"，并带上日历实际覆盖到哪天 ——
    否则用户看到的还是一个没有解释的空档。
    """
    import server
    monkeypatch.setattr(server, "_calendar_max_day", lambda: "2026-12-31")
    calls, recorded = one_round(trade=None, rounds=5)
    assert calls == []                                   # 判不了就一个子进程都不起
    assert [(n, ok, d["verdict"]) for n, ok, d in recorded] == [
        ("daily_close", False, "unknown_calendar")]      # 五轮只记一次
    assert "不敢猜" in recorded[0][2]["reason"] and "2026-12-31" in recorded[0][2]["reason"]


def test_时点配置坏了回落默认且不炸(monkeypatch):
    import server
    for bad, want in (("", "15:59"), ("25:99", "15:59"), ("15:5", "15:59"),
                      ("垃圾", "15:59"), ("15:59", "15:59")):
        monkeypatch.setenv("DAILY_CLOSE_AT", bad)
        assert server._daily_close_slot() == want


def test_测试进程与显式关闭都不起线程(monkeypatch):
    import server
    import threading as th
    started = []

    class _T:
        def __init__(self, *a, **k):
            pass

        def start(self):
            started.append("daily-close")

    real = th.Thread
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    monkeypatch.setattr(server, "_daily_close_thread_started", False)
    th.Thread = _T
    try:
        server._start_daily_close()                       # pytest 里必须不真起
        monkeypatch.delenv("PYTEST_CURRENT_TEST")
        monkeypatch.setenv("VIBE_DAILY_CLOSE", "0")
        server._start_daily_close()                       # 显式关也不起
        monkeypatch.delenv("VIBE_DAILY_CLOSE")
        server._start_daily_close()
        server._start_daily_close()                       # 幂等：不许起第二个
    finally:
        th.Thread = real
    assert started == ["daily-close"]


# ---- 提前拉起与「只在交易日执行」（用户 2026-10-07 要的两格）---------------------

def test_提前拉起只发一次且发在链之前(one_round):
    """lead=20 → 15:39 那一次心跳发拉起，15:59 才发链。
    **一天只发一次**：连续几分钟都落在提前窗口里，重复发就是连着点桌面。"""
    calls, _ = one_round(rounds=30, lead=20, start="15:35")
    launches = [c for c in calls if "--launch-only" in c]
    chains = [c for c in calls if "--launch-only" not in c]
    assert len(launches) == 1, calls
    assert launches[0][0] == "ingest.jobs.daily_close_sync"
    assert chains and chains[0][0] == "ingest.jobs.daily_close_sync", "拉起之后链照跑"
    assert calls.index(launches[0]) < calls.index(chains[0]), "拉起必须早于链"


def test_提前拉不起作用时非交易日不碰界面(one_round):
    """lead 开着但今天不是交易日：一次界面动作都不该发（默认 `trading_days_only=true`）。"""
    calls, _ = one_round(rounds=30, lead=20, start="15:35", trade=False)
    assert calls == [], calls


def test_提前拉起在判不了日历时也不动(one_round):
    # rounds=25：从 15:40 要走满提前窗口（到 15:58）再进链的窗口（15:59+）才看得到那句记账
    calls, recorded = one_round(rounds=25, lead=20, start="15:40", trade=None)
    assert [c for c in calls if "--launch-only" in c] == []
    assert any(r.get("verdict") == "unknown_calendar" for _n, _ok, r in recorded), recorded


def test_关掉只在交易日之后非交易日照跑一次(one_round):
    """这一档的语义是"每天都发一次"，不是"每天都会有数"：段脚本自己按日历拒写。"""
    calls, _ = one_round(rounds=3, lead=0, trade=False, only_trade=False)
    assert [c[0] for c in calls] == ["ingest.jobs.daily_close_sync", "ingest.jobs.e1_fill_all"]


def test_开着只在交易日时非交易日一子进程都不起(one_round):
    calls, _ = one_round(rounds=3, lead=0, trade=False, only_trade=True)
    assert calls == []


def test_拉起这一步不占链的账(one_round):
    """拉起只是前置动作：它不能把 `daily_close` 的"最近跑过/成功"刷成一次没发生过的链。"""
    _calls, recorded = one_round(rounds=30, lead=20, start="15:35")
    assert all(r.get("verdict") != "launch_lead" for _n, _ok, r in recorded), recorded
    assert [(n, ok) for n, ok, _ in recorded] == [("daily_close", True), ("fill_all", True)]


def test_那句今天不跑说的是哪一层依据(monkeypatch):
    """2027 年起的周末与跨年那几天，"非交易日"是星期几或行情时间戳答的，不是日历。
    日志再写"判据是真日历"就是一句会自己变假的措辞 —— 依据要跟着读数走，
    探测本身炸了也不能把调度线程带走。"""
    from duanxian import pg

    import server

    for layer, expect in (("calendar", "交易日历"), ("weekend", "星期几自判"),
                          ("quote", "行情的时间戳")):
        monkeypatch.setattr(pg, "trade_day_evidence", lambda d, now=None, _l=layer: _l)
        assert expect in server._trade_day_why(dt.date(2027, 1, 4))

    def boom(day, now=None):
        raise RuntimeError("库读不到")

    monkeypatch.setattr(pg, "trade_day_evidence", boom)
    assert server._trade_day_why(dt.date(2027, 1, 4)) == "判据读不到"


def test_链收工后自动关客户端_默认关且每天只试一次(monkeypatch):
    """用户 10-08 同意加这一档，但**默认必须是关**：客户端保持登录是历史决定
    （TQ 直取要它在、次日免重登），而误杀正在写 vipdoc 的客户端代价极大。"""
    import server

    from duanxian import jobs as jstore
    merged, _w = jstore.settings("daily_close", jstore.DAILY_CLOSE_DEFAULTS)
    assert merged["close_client_after"] is False, "默认关：这一档必须由人显式打开"
    calls = []
    monkeypatch.setattr(server, "_close_tdx_client",
                        lambda **k: calls.append(1) or {"acted": True, "closed": True})
    server._client_closed_day = None
    assert server._maybe_close_client("2026-10-08", ran_fill_all=True) is None
    assert calls == [], "开关关着就一扇窗都不碰"
    jstore.write_setting("daily_close", via="test", close_client_after=True)
    server._client_closed_day = None
    out = server._maybe_close_client("2026-10-08", ran_fill_all=True)
    assert out and out["closed"] is True and calls == [1]
    # 一天只试一次：收工分支每分钟都过这里，不设闸就会一直给客户端发关闭消息
    assert server._maybe_close_client("2026-10-08", ran_fill_all=True) is None
    assert len(calls) == 1
    # 而链根本没跑过（加工段没跑）时不许动桌面
    server._client_closed_day = None
    assert server._maybe_close_client("2026-10-09", ran_fill_all=False) is None
    assert len(calls) == 1


def test_关客户端这件事不发强杀也不猜按钮():
    """三条守则钉源码：只发一次 `close_window(syscommand=True)`；
    没有 `taskkill` / `kill_tree` / `/F` / `/IM`；在同步或 vipdoc 还在写就不许动。

    ⚠️ 2026-10-13 更正过的一条：这条原来还断言"决定框只**读**不点"。用户点名要的是
    "需要时才运行 + 定时任务不要弹窗、后台静默执行"，只读不点 = 每天要人对着
    「退出 / 重新登录 / 取消」点一次，那条模式就还是半真的。
    所以现在的判据不是"绝不点"，而是**只准按安全形状点**：点击收在
    `_answer_exit_confirm` 一个出口里，必须 `exact=True`（全文等于「退出」，不许子串）、
    必须过 `NEVER_CLICK` 黑名单、pytest 进程里硬挡。这条断言因此**收紧**而不是放宽：
    想绕过那个出口直接在本体里点按钮，照样红。
    """
    import inspect

    import server

    body = inspect.getsource(server._close_tdx_client)
    assert "close_window(frames[0].hwnd, syscommand=True)" in body
    for forbidden in ("taskkill", "kill_tree", '"/F"', "/IM"):
        assert forbidden not in body, f"关客户端不许用 {forbidden}"
    assert "bm_click" not in body, "点击只能走 `_answer_exit_confirm` 那一个出口"
    assert "_answer_exit_confirm" in body and "exit_confirm_click" in body, \
        "答确认框这件事要有开关，不许无条件点"
    click = inspect.getsource(server._answer_exit_confirm)
    assert "exact=True" in click, "按钮必须全文相等才点（10-06 子串匹配按到「取消下载」掉了数据）"
    assert "NEVER_CLICK" in click and "PYTEST_CURRENT_TEST" in click
    assert "confirm_boxes" in body, "下载那一类决定框仍要读出来交人工"
    assert "sync_running" in body and "_vipdoc_still_growing" in body, "在同步/还在写就不许动"
    guard = inspect.getsource(server._maybe_close_client)
    assert "ran_fill_all" in guard


def test_收工那一刻真的接了这个动作():
    """判据写完没接到调用点 = "看着有其实没接"（这一轮已经抓到三次）。"""
    import pathlib

    import server

    body = pathlib.Path(server.__file__).read_text(encoding="utf-8")
    sched = body.split("def _daily_close_scheduler")[1]
    assert "_maybe_close_client(today, ran_fill_all=" in sched.split("def ")[0], \
        "链收工分支里必须调用它"
    cfg = pathlib.Path(server.__file__).resolve().parent / "vr" / "app.py"
    keys = cfg.read_text(encoding="utf-8")
    assert '"close_client_after"' in keys, "写入面要认这个键，否则页面上是个改了不生效的框"
