"""`/api/jobs` 数据底的回归。钉的是三件"会误导向"的事，不是字段个数。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "vr"))   # 末尾：插最前会让 vr/app.py 抢走顶层 app/ 包
import jobsboard  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(tmp_path / "datahome"))
    monkeypatch.setenv("VIBE_BACKUP_DIR", str(tmp_path / "backup"))
    return tmp_path


def _touch(path: Path, text="x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_PG_断开时_build_不抛而是给可读性标记(home, monkeypatch):
    """conftest 拦了出站 socket，这里就是"库读不到"的现实形状。"""
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: False)
    snap = jobsboard.build()
    assert snap["jobs_available"] is True and isinstance(snap["jobs"], list)
    assert snap["jobs"], "库读不到也要交出本进程自己的作业，不能交空表"


def test_文件侧证据算得出_news_的最近产物(home, monkeypatch):
    _touch(home / "datahome" / "news" / "news-2026-09-25.jsonl")
    _touch(home / "datahome" / "news" / "cursors.json", '{"em": "1"}')
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: False)
    news = next(j for j in jobsboard.build()["jobs"] if j["key"] == "news")
    assert news["last_data_date"] == "2026-09-25"
    assert news["last_run_at"]
    assert "文件侧证据" in news["basis"]      # 依据必须自报，不能看着像 state.json 出来的


def test_ft_登记项走_available_false_不借用全局开关(home, monkeypatch):
    """全局 scheduler 只描述本进程宿主；FT 作业若借它解释，会被整页判成"未调度"。"""
    import pandas as pd
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: True)
    monkeypatch.setattr(jobsboard._pg, "fetch_df", lambda sql, *a, **k: (
        pd.DataFrame([{"job_key": "sync_daily", "name": "日线入库", "kind": "ingest",
                       "enabled": True, "trigger_kind": "cron", "cron_expr": "30 17 * * *",
                       "only_trade_day": True, "last_fire_at": None, "next_fire_at": None,
                       "last_success_date": None, "last_result": "skip",
                       "consecutive_fail": 0, "missed_count": 0,
                       "note": "调度器总开关未打开（scheduler_enabled=false）"}])
        if "config.job" in sql else None))
    snap = jobsboard.build()
    job = next(j for j in snap["jobs"] if j["key"] == "sync_daily")
    assert job["origin"] == "financetool" and job["runner"] == "ft-scheduler"
    assert job["available"] is False and job["reason"]
    assert "consecutive_fail=0" in job["reason"] and "不等于健康" in job["reason"]
    assert job["last_ok_at"] is None          # 只有日期没有墙钟时刻 → 不拼假时间
    assert snap["scheduler"]["enabled"] is True


def test_没有可恢复性标记时老实说未验(home, monkeypatch):
    monkeypatch.setattr(jobsboard, "READCHECK_MARKER", home / "nope.json")
    dump = home / "backup" / "daily" / "daily_20260925_000000.dump"
    _touch(dump)
    assert jobsboard._readcheck(dump) == "未验"
    marker = home / "readcheck.json"
    monkeypatch.setattr(jobsboard, "READCHECK_MARKER", marker)
    _touch(marker, json.dumps({str(dump): {"ok": False, "why": "读不开：Permission denied",
                                           "checked_at": 1}}))
    assert "读不开" in jobsboard._readcheck(dump)


def test_没有执行体的作业不给触发(home, monkeypatch):
    status, payload = jobsboard.trigger("fundflow")
    assert status == 403 and payload["reason"]
    status, payload = jobsboard.trigger("sync_daily")
    assert status == 403


def test_NaN_不许把接口打成_500(home, monkeypatch):
    """回归：pandas 把 NULL 读成 NaN，json.dumps 遇 NaN 直接抛 → 页面 500（2026-09-25 线上事故）。"""
    import pandas as pd
    jobs = pd.DataFrame([{"job_key": "sync_daily", "name": "x", "kind": "ingest", "enabled": True,
                          "trigger_kind": "cron", "cron_expr": float("nan"), "only_trade_day": True,
                          "last_fire_at": float("nan"), "next_fire_at": pd.NaT,
                          "last_success_date": pd.NaT, "last_result": "skip",
                          "consecutive_fail": 0, "missed_count": 0, "note": float("nan")}])
    hb = pd.DataFrame([{"role": "scheduler", "pid": float("nan"),
                        "heartbeat_at": float("nan"), "meta": {"fired": 0}}])

    def fake(sql, *a, **k):
        if "config.job" in sql:
            return jobs
        return hb if "scheduler_heartbeat" in sql else None

    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: True)
    monkeypatch.setattr(jobsboard._pg, "fetch_df", fake)
    snap = jobsboard.build()
    text = json.dumps(snap, ensure_ascii=False, allow_nan=False)   # 与 FastAPI 同行为
    # 只抓独立的 nan/NaT/inf 词元：`FinanceTool` 里就含 "nan"，用子串会自己骗自己
    ghost = re.findall(r'"[^"]*(nan|NaT|inf)[^"]*"', text)
    assert not ghost, ghost
    job = next(j for j in snap["jobs"] if j["key"] == "sync_daily")
    assert job["schedule_spec"] is None and job["last_run_at"] is None
    assert not re.search(r"(nan|NaT)", job["reason"])   # 拼进文案的 NaN 才是真正的坑


def test_收盘前不把今天当基准(home, monkeypatch):
    """`trade_day_cutoff` 没生效的话，每个上午都会显示"落后 1 个交易日"。"""
    import datetime as real_dt
    seen = []

    def fake(sql, params=(), *a, **k):
        if "trade_day_cutoff" in sql:
            return None
        if "is_open and calendar_date <=" in sql:
            seen.append(params[0] if params else None)
            return None
        return None

    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: True)
    monkeypatch.setattr(jobsboard._pg, "fetch_df", fake)
    jobsboard._expected_trade_day(real_dt.datetime(2026, 9, 25, 10, 30))
    jobsboard._expected_trade_day(real_dt.datetime(2026, 9, 25, 16, 30))
    assert seen == ["2026-09-24", "2026-09-25"]


def _state(home, name, entry):
    from duanxian import jobs as jstore
    all_state = jstore.state()
    all_state[name] = entry
    jstore.atomic_write_json(jstore.state_path(), all_state)


def test_获取段停住时加工段说未起但不编时间戳(home, monkeypatch):
    """上游今天按设计停下 → 下游一次都没跑。两件事必须同时成立：
    页面上要说出"未起"和原因（否则看着像没人管的漏跑），
    但 `last_run_at` **必须留 null**（契约 03 §3：没跑过就是 null，
    编一个时刻出来会让人去查一个根本不存在的失败）。"""
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: False)
    import datetime as real_dt
    from duanxian import jobs as jstore
    _state(home, "daily_close", {
        "last_run_at": f"{real_dt.date(2026, 9, 28)} 15:59:02", "last_ok_at": None,
        "last_status": "fail", "consecutive_failures": 1,
        "last_error": "数据没到位且自动手段用尽",
        "last_detail": {"verdict": "stop", "reason": "数据没到位，要人来处理"}})
    monkeypatch.setattr(jobsboard, "china_now",
                        lambda: real_dt.datetime(2026, 9, 28, 16, 0))
    fill = next(j for j in jobsboard.build()["jobs"] if j["key"] == "fill_all")
    assert fill["state"].startswith("未起") and "daily_close" in fill["state"]
    assert fill["last_error"] == "数据没到位，要人来处理"
    assert fill["last_run_at"] is None and fill["last_ok_at"] is None
    assert fill["consecutive_failures"] is None      # 它自己没失败过，别替它记


def test_上游昨天停住不影响今天的加工段读数(home, monkeypatch):
    """「未起」只能挂在**同一天**的上游停住上：拿昨天的停住说今天的事，
    等于让一次人工中断污染后面所有天。"""
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: False)
    import datetime as real_dt
    _state(home, "daily_close", {
        "last_run_at": "2026-09-27 15:59:02", "last_ok_at": None,
        "last_status": "fail", "consecutive_failures": 1,
        "last_error": "昨天的", "last_detail": {"verdict": "stop", "reason": "昨天的"}})
    _state(home, "fill_all", {"last_run_at": "2026-09-27 16:20:00",
                              "last_ok_at": "2026-09-27 16:20:00", "last_status": "ok",
                              "consecutive_failures": 0, "last_error": None})
    monkeypatch.setattr(jobsboard, "china_now",
                        lambda: real_dt.datetime(2026, 9, 28, 9, 0))
    fill = next(j for j in jobsboard.build()["jobs"] if j["key"] == "fill_all")
    assert fill["state"] == "正常" and "未起" not in fill["state"]


def test_页面报的时刻就是宿主在用的那一个(home, monkeypatch):
    """两处各读各的就会"显示 15:59、实际按别的跑"。这里注入一个非默认值，
    要求 `schedule_spec` 与 `next_due_at` 都跟着动，且说出来源。"""
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: False)
    monkeypatch.setenv("DAILY_CLOSE_AT", "16:40")
    import datetime as real_dt
    monkeypatch.setattr(jobsboard, "china_now",
                        lambda: real_dt.datetime(2026, 9, 28, 15, 0))
    snap = jobsboard.build()
    close = next(j for j in snap["jobs"] if j["key"] == "daily_close")
    assert "16:40" in close["schedule_spec"] and "环境变量" in close["schedule_spec"]
    assert close["next_due_at"] == "2026-09-28 16:40"


def test_时点优先级只有一份读数(home):
    """作业配置 > 环境变量 > 默认；非法值出声并回落，绝不静默采用。"""
    from duanxian import jobs as jstore
    assert jstore.run_at() == ("15:59", "默认值（没配 run_at）")
    jstore.write_setting("daily_close", run_at="16:05")
    got = jstore.run_at()
    assert got[0] == "16:05" and "作业配置" in got[1]
    jstore.write_setting("daily_close", run_at=None)          # 清空 = 交还给上层
    jstore.load_config()
    assert jstore.run_at()[0] == "15:59"


class _T:
    def __init__(self, name):
        self.name = name


def test_宿主缺哪条循环必须说出来(home, monkeypatch):
    """"宿主在跑"这种整体说法掩盖单条循环的死活：改完 15:59 的宿主却没重启，
    页面照样是绿的。这里把 `threading.enumerate` 换成"只有三条"，
    要求缺失项点名、并且 note 里能读出来。"""
    import threading as th
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: False)
    monkeypatch.setattr(th, "enumerate",
                        lambda: [_T("MainThread"), _T("newswire"), _T("intraday"),
                                 _T("coverage-precompute")])
    snap = jobsboard.build()
    sched = snap["scheduler"]
    alive = ["intraday", "newswire", "coverage-precompute"]
    # 期望值从 `HOST_LOOPS` 现算，不写死条数：加一条循环（2026-10-06 加了 `tdx-watch`）
    # 就把这些断言悄悄变红，那种红只会让人去改测试而不是去看真相。
    assert sched["host_loops"] == alive
    # 10-08 起的读法：缺 = **该活着却没有**；被开关明确关掉的（默认关的 `ths-cloud-sync`）
    # 不算缺，另有一格。少了这一层，一条按设计停用的循环会把缺勤表永久涂红。
    off = set(jobsboard.switched_off_loops())
    # 按需起的那几条也不算缺（夜里没人订阅盯盘时线程本来就不该在），但它必须**另有一格**，
    # 否则"其实从没起过"会躲在豁免里 —— 见 host_loops_ondemand 与下面那句 note 断言。
    assert sched["host_loops_missing"] == [n for n in jobsboard.HOST_LOOPS
                                          if n not in alive and n not in off
                                          and n not in jobsboard.ON_DEMAND_LOOPS]
    assert "缺 %d 条" % len(sched["host_loops_missing"]) in sched["note"]
    assert "daily-close" in sched["note"]
    assert set(n["name"] for n in sched["host_loops_ondemand"]) == set(jobsboard.ON_DEMAND_LOOPS)
    assert all(not n["alive"] for n in sched["host_loops_ondemand"]), "这个桩里没给盯盘线程"
    assert "按需起" in sched["note"] and "没人订阅" in sched["note"]


def test_常驻循环全在时note说满(home, monkeypatch):
    import threading as th
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: False)
    monkeypatch.setattr(th, "enumerate", lambda: [_T(n) for n in jobsboard.HOST_LOOPS])
    sched = jobsboard.build()["scheduler"]
    # 分母是**该一直活着**那几条：按需起的（`watchtower`）不进这个分数，否则"9/10"会让人
    # 以为缺了一条，而它只是还没人订阅。它另走 host_loops_ondemand 那一格。
    n = len([x for x in jobsboard.HOST_LOOPS if x not in jobsboard.ON_DEMAND_LOOPS])
    assert sched["host_loops_missing"] == [] and "%d/%d" % (n, n) in sched["note"]
    assert all(x["alive"] for x in sched["host_loops_ondemand"]), "线程都在时按需那条要报「已起」"
    assert "已起" in sched["note"]
    # 通达信那条**只探测**的看护必须在册：它以前是"代码写好了没人调用"，
    # 不在册就等于缺了也没人报（2026-10-06 的实测根因）。
    assert "tdx-watch" in jobsboard.HOST_LOOPS


def test_开关关着就不许再报一个将要运行的时刻(home, monkeypatch):
    """关掉获取段之后页面还显示"明天 15:59 要跑"，读的人只会得出不敢行动的结论。

    2026-09-28 实测：系统配置把 15:59 链接进常驻 uvicorn 后，我按用户裁定把两段都关了，
    `/api/jobs` 里 `daily_close` 的 `enabled` 已经是 `false`，`next_due_at` 却还是
    `2026-09-28 15:59` —— 看起来像"关了但它还会自己动桌面"。关着就交 null。
    """
    import datetime as real_dt
    from duanxian import jobs as jstore

    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: False)
    monkeypatch.setattr(jobsboard, "china_now", lambda: real_dt.datetime(2026, 9, 28, 15, 0))
    jstore.set_enabled('daily_close', False)
    try:
        snap = jobsboard.build()
        close = next(j for j in snap["jobs"] if j["key"] == "daily_close")
        assert close["enabled"] is False
        assert close["next_due_at"] is None, "关着的作业不许还拥有'下一次该跑'的时刻"
        assert close["schedule_spec"], "但时刻本身要说清是多少，回来时不用重新猜"
    finally:
        jstore.set_enabled('daily_close', True)


def test_开关面板交出留痕与今天会不会跑(home, monkeypatch):
    """这一格的存在理由就是"被关了要说清是谁关的"：开关、时点来源、留痕、理由，四样都必须在。"""
    import datetime as real_dt
    from duanxian import jobs as jstore
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: True)
    monkeypatch.setattr(jobsboard._pg, "trade_day_open", lambda d: True)
    monkeypatch.setattr(threading := __import__("threading"), "enumerate",
                        lambda: [type("T", (), {"name": "daily-close"})()])
    jstore.write_setting("daily_close", via="cli:jobs.py set", note="周末不改代码，试完开回")
    jstore.set_enabled("daily_close", False, via="cli:jobs.py disable")
    st = jobsboard.chain_status(now=real_dt.datetime(2026, 9, 28, 16, 0))
    close = next(s for s in st["stages"] if s["key"] == "daily_close")
    assert close["enabled"] is False and close["default_enabled"] is True
    # 留痕是**最后一个写的人**：先写理由、再关掉，`changed_by` 就该是 disable 那条
    assert close["changed_by"] == "cli:jobs.py disable" and close["changed_at"]
    assert close["note"] == "周末不改代码，试完开回"
    assert st["host_loop_alive"] is True and st["is_trade_day"] is True
    assert st["slot"] == "15:59" and "默认值" in st["slot_source"]
    # 两段都关着 → 今天不会跑；只关一段不算（加工段自己会补）
    assert st["today_will_run"] is True


def test_页面改开关要署名并留痕(home, monkeypatch):
    from duanxian import jobs as jstore
    code, resp = jobsboard.set_switch("fill_all", False, via="api:/api/jobs/chain@127.0.0.1")
    assert code == 200 and "不会自己恢复" in resp["reason"]
    note = jstore.change_note("fill_all")
    assert note["changed_by"] == "api:/api/jobs/chain@127.0.0.1"
    assert jstore.settings("fill_all", jstore.FILL_ALL_DEFAULTS)[0]["enabled"] is False
    # 未知作业不猜
    assert jobsboard.set_switch("nope", True, via="api")[0] == 404


def test_时点只有获取段有且非法值被拒(home):
    from duanxian import jobs as jstore
    assert jobsboard.set_slot("fill_all", "16:30", via="api")[0] == 400
    code, resp = jobsboard.set_slot("daily_close", "25:99", via="api")
    assert code == 400 and "HH:MM" in resp["reason"]
    assert jstore.settings("daily_close", jstore.DAILY_CLOSE_DEFAULTS)[0]["run_at"] == "15:59"
    assert jobsboard.set_slot("daily_close", "16:30", via="api")[0] == 200
    assert jstore.run_at() == ("16:30", "作业配置 daily_close.run_at")


def test_理由太长要拒且清空就是交还默认(home):
    from duanxian import jobs as jstore
    assert jobsboard.set_note("daily_close", "长" * 121, via="api")[0] == 400
    jobsboard.set_note("daily_close", "试一天", via="api")
    assert jstore.settings("daily_close", jstore.DAILY_CLOSE_DEFAULTS)[1] == []   # note 是已声明键，不该报"未知配置项"
    jobsboard.set_note("daily_close", "", via="api")
    assert "_changed_by" in (jstore.load_config().get("daily_close") or {})


def test_开关为关不会报未知配置项(home):
    """留痕键（`_changed_at` 这种）不是配置项，混进 `settings()` 的告警里
    会让页面上每轮都挂一句假警告。"""
    from duanxian import jobs as jstore
    jstore.write_setting("daily_close", via="cli:test")
    merged, warns = jstore.settings("daily_close", jstore.DAILY_CLOSE_DEFAULTS)
    assert warns == [] and merged["enabled"] is True


# ------------------------------------------------ 常驻循环的开关面板（10-06 补充需求）
def test_每条在册循环都必须给关得动的入口(home, monkeypatch):
    """17:3x 的形状是"页面上看得见 7 条循环，其中 5 条没有开关"，只剩环境变量总闸 ——
    那对非技术用户等于没有。写入面必须认整张在册表，而不是只认收盘链那两段。"""
    from duanxian import jobs as jstore
    for key in sorted(jstore.CLOSE_DEFAULTS):
        status, resp = jobsboard.set_switch(key, False, via="test")
        assert status == 200, f"{key} 关不动：{resp}"
        status, resp = jobsboard.set_switch(key, True, via="test")
        assert status == 200, f"{key} 开不回：{resp}"
    status, resp = jobsboard.set_switch("nonexistent_job", False, via="test")
    assert status == 404 and "CLOSE_DEFAULTS" in resp["reason"]


def test_每条在册循环的代价都必须逐条交出去(home):
    """`fundflow` 当天不采永久少一天，`coverage` 只是首屏要现场等 —— 同一个小开关代价差一个量级，
    所以代价必须跟着开关一起上页面，而不是让人自己猜。

    条数**跟着 `LOOP_JOBS` 走**（写死一个 7 的话，加一条就要改三处，10-07 就撞过一次）；
    另有一条专门盯"清单确实长了没有"：`watchtower` 与 `intraday` 是 10-07 补进来的两条
    —— 它们早就在跑、早就在记账，只是没人看得见也关不掉。
    """
    data = jobsboard.loop_switches()
    # 条数**只跟着 `LOOP_JOBS` 走**，不再钉字面量：10-08 加第 9 条（同花顺云端同步）时，
    # 写死 8 的断言会把"加了一条"这件事变成两处红 —— 那正是它早该改掉的原因。
    assert len(data["loops"]) == len(jobsboard.LOOP_JOBS)
    assert {r["key"] for r in data["loops"]} >= {"watchtower", "intraday"}
    for row in data["loops"]:
        assert row["off_effect"], row["key"]
        assert set(row) >= {"key", "loop", "alive", "switchable", "enabled", "cadence",
                            "last_run_at", "last_ok_at", "never_recorded", "changed_at", "changed_by"}
    by = {r["key"]: r for r in data["loops"]}
    assert "永久少一天" in by["fundflow"]["off_effect"]
    assert "永久少一天" in by["daily_close"]["off_effect"]


def test_探测循环不许配时点(home):
    """`tdx_watch` 是"每两分钟看一眼"，不是"几点跑一次"。给它一个时点框 = 页面上
    多一个改了也不生效的输入框（最难查的假生效）。"""
    status, resp = jobsboard.set_slot("tdx_watch", "08:00", via="test")
    assert status == 400 and "没有时点" in resp["reason"]
    status, resp = jobsboard.set_slot("fill_all", "08:00", via="test")
    assert status == 400 and "daily_close.run_at" in resp["reason"]
    status, resp = jobsboard.set_slot("coverage", "07:30", via="test")
    assert status == 200, resp
    row = next(r for r in jobsboard.loop_switches()["loops"] if r["key"] == "coverage")
    assert row["run_at"] == "07:30" and row["run_at_source"] == "作业配置 coverage.run_at"
    # 面板上只有真有 run_at 的键才该渲染那个框
    by = {r["key"]: r for r in jobsboard.loop_switches()["loops"]}
    assert by["tdx_watch"]["has_slot"] is False and by["coverage"]["has_slot"] is True


def test_活着但零笔记账必须单独现形(home, monkeypatch):
    """判"循环在不在"与判"它记不记账"必须分开交 —— 合在一起就藏住 10-06 那个 `intraday`：
    线程活着，`state.json` 里连键都没有。"""
    monkeypatch.setattr(jobsboard, "_live_loops",
                        lambda: (list(jobsboard.HOST_LOOPS), []))
    _state(home, "watchlist", {"last_run_at": "2026-10-05 20:40:00",
                               "last_ok_at": "2026-10-05 20:40:00"})
    by = {r["key"]: r for r in jobsboard.loop_switches()["loops"]}
    assert by["watchlist"]["alive"] and by["watchlist"]["never_recorded"] is False
    assert by["intraday"]["alive"] and by["intraday"]["never_recorded"] is True
    # 10-07 起 `intraday` 也进了 CLOSE_DEFAULTS：以前这条是 `switchable is False`
    # （"只能看不能关"），而现在宿主每轮真读这个开关 —— 面板上的框不再是假的。
    assert by["intraday"]["switchable"] is True
    assert by["intraday"]["has_slot"] is False           # 六个槽位是常量，不许给一个时点框
    assert "槽位型" in by["intraday"]["cadence"]
    wt = by["watchtower"]
    assert wt["alive"] is True and wt["switchable"] is True   # `_live_loops` 被打桩成全在
    assert wt["cadence"].startswith("间隔型") and wt["unswitchable_reason"] is None


def test_循环清单与记账键配对只有一份(home):
    """`manage.py::declared_loops` 现在读的是这张成对表；名字清单与配对表分叉就会
    出现"体检说 7 条齐、面板上只有 5 条"。"""
    from scripts import manage
    assert [n for n, _k, _l, _o in jobsboard.LOOP_JOBS] == list(jobsboard.HOST_LOOPS)
    pairs, why = manage.declared_loop_jobs(manage.ROOT)
    assert not why and pairs == [(n, k) for n, k, _l, _o in jobsboard.LOOP_JOBS]


# ------------------------------------------------ /api/jobs/loops 这两条端点
@pytest.fixture()
def client(home):
    """走 `server.app`，**不要** `sys.path.insert(0, "vr") + import app`：
    那样模块名 `app` 会被 `vr/app.py` 抢走，顶层 `app/` 包从此 import 不了
    （`ModuleNotFoundError: 'app' is not a package`），本文件顶部那条注释说的就是这回事。
    10-06 实测：插最前的写法会让同批跑的 `test_auto_execution_hardening.py` 红 22 条。"""
    from fastapi.testclient import TestClient
    import server
    return TestClient(server.app, base_url="http://127.0.0.1:8910", client=("127.0.0.1", 1))


def test_loops_端点给全在册每条并带代价与留痕(client):
    body = client.get("/api/jobs/loops").json()["data"]
    assert len(body["loops"]) == len(jobsboard.LOOP_JOBS) and body["can_edit"] is True
    assert body["declared_count"] == len(jobsboard.LOOP_JOBS)   # 条数不钉字面量（10-08 起有第 9 条）
    row = next(r for r in body["loops"] if r["key"] == "tdx_watch")
    assert row["has_slot"] is False and row["switchable"] is True
    assert "探测" in row["name"] and row["off_effect"]
    # 「全量/增量」只有自选那条该有：给一个没人读的键配下拉框 = 页面上多一个假生效
    wl = next(r for r in body["loops"] if r["key"] == "watchlist")
    assert wl["sync_mode"] == "full" and set(wl["sync_mode_choices"]) == {"full", "append"}
    assert all(r["sync_mode"] is None for r in body["loops"] if r["key"] != "watchlist")


def test_loops_关掉一条要立刻在读数里看得见(client, home):
    """关完必须能在**同一份读数**里看到"关着 + 谁关的"，否则又是一个改得动看不见的小开关。"""
    before = client.get("/api/jobs/loops").json()["data"]
    assert next(r for r in before["loops"] if r["key"] == "coverage")["enabled"] is True
    out = client.post("/api/jobs/loops", json={"key": "coverage", "enabled": False})
    assert out.status_code == 200, out.text
    row = next(r for r in out.json()["data"]["loops"] if r["key"] == "coverage")
    assert row["enabled"] is False
    assert row["changed_by"].startswith("api:/api/jobs/loops@127.0.0.1")
    assert row["note"] is None                          # 没写过理由就该是 null，不是空串充数
    # 再开回来（测试不许把真配置留在关着的形状；home 是临时目录，但这里连读写链路一起验）
    assert client.post("/api/jobs/loops", json={"key": "coverage", "enabled": True}).status_code == 200


def test_loops_一次只改一件事且未知键拒(client):
    assert client.post("/api/jobs/loops",
                       json={"key": "coverage", "enabled": False, "note": "两个一起"}).status_code == 400
    assert client.post("/api/jobs/loops",
                       json={"key": "nope", "enabled": False}).status_code == 404


# ---------------------------------------------------------------- /api/tdx/watch 与 /api/tdx/status
# 这两格原先在页面上挂着"接口 404"：看护循环已经在跑，而界面看不见就等于没跑。

def _probe(running=False, login_box=False, sync='没有 TPyth* 进程'):
    return lambda: {'at': '2026-10-06T21:40:00+08:00', 'running': running,
                    'pid': 4321 if running else None, 'window': None,
                    'minimized': None, 'login_box': login_box, 'sync': sync}


def test_watch_探不到又没记录时available是false不编数字(client, monkeypatch):
    from app.tdx_client import watchman
    def boom():
        raise OSError('枚举窗口失败')
    monkeypatch.setattr(watchman, 'probe', boom)
    d = client.get('/api/tdx/watch').json()['data']
    assert d['available'] is False and '一条看护记录都没有' in d['reason']
    assert d.get('process_running') is None            # 不是 False：没探到 ≠ 没在跑


def test_watch_现问探测的字段与三态(client, monkeypatch):
    from app.tdx_client import watchman
    monkeypatch.setattr(watchman, 'probe', _probe(running=True, sync='TPython.exe(777)'))
    d = client.get('/api/tdx/watch').json()['data']
    assert d['available'] is True and d['source'] == 'live'
    assert d['process_running'] is True and d['login_dialog'] is False
    assert d['syncing'] is True and d['sync'] == 'TPython.exe(777)'
    # 主窗口枚举不到时 window_visible 是 null，不许当成"不可见"再当成"关了"
    assert d['window_visible'] is None
    # 两条界面自动化开关必须上屏（用户 10-06 点头开的，页面得说出现在是什么）
    assert d['auto_launch'] is True and d['auto_restart'] is True
    assert d['trail']['pending'] == []


def test_watch_带出待人工清单(client, monkeypatch):
    """清单里没做成的那些必须在这一格看得见 —— 用户要的是"我自己能找出来"。"""
    from app.tdx_client import watchman
    from duanxian import gui_trail
    monkeypatch.setattr(watchman, 'probe', _probe())
    gui_trail.add(step='s5_dialog', what='打开盘后数据下载', status='need_human',
                  detail='菜单被折叠', target='2026-10-06')
    d = client.get('/api/tdx/watch').json()['data']
    assert d['trail']['count'] == 1 and d['trail']['pending'][0]['step'] == 's5_dialog'
    assert '盘后数据下载' in d['trail']['pending'][0]['manual']


def test_watch的note只认这一次探测不兜上一轮的旧句子(client, monkeypatch):
    """10-13 实测的自相矛盾：08:09 客户端被自动关掉之后，同一个响应里
    `process_running=false`，而 `note` 还是上一轮记账那句「在跑（pid 40672）」。
    根因：`watchman.probe()` 根本不附 `reason`，而那行是 `probe.reason or detail.reason` ⇒
    永远兜到旧记录。**现问探测成功时，note 只准由这一次的探测说话**；
    只有探测真失败（`source=cached`）才允许引用旧记录，而且要说清是旧的。"""
    from app.tdx_client import watchman
    from duanxian import jobs as jstore

    stale = "在跑（pid 40672）· 同步子进程：没有 TPyth* 进程"
    jstore.record("tdx_watch", ok=True, detail={"reason": stale, "pid": 40672})

    monkeypatch.setattr(watchman, "probe", _probe(running=False))
    d = client.get('/api/tdx/watch').json()['data']
    assert d['source'] == 'live' and d['process_running'] is False
    assert '40672' not in (d['note'] or ''), "旧记录里的 pid 混进了现问读数的说明"
    assert '没在跑' in d['note'], d['note']

    monkeypatch.setattr(watchman, "probe", _probe(running=True))
    d2 = client.get('/api/tdx/watch').json()['data']
    assert d2['process_running'] is True and '4321' in d2['note'], d2['note']

    def boom():
        raise OSError('枚举窗口失败')
    monkeypatch.setattr(watchman, "probe", boom)
    d3 = client.get('/api/tdx/watch').json()['data']
    assert d3['source'] == 'cached' and '40672' in d3['note']
    assert '上一次记录' in d3['note'], "引用旧读数必须当场标出来是旧的"


def test_status_探不到的两格恒为空不拿假值顶(client, monkeypatch):
    from app.tdx_client import tq_bridge, watchman
    monkeypatch.setattr(watchman, 'probe', _probe())
    monkeypatch.setattr(tq_bridge, 'available', lambda: (False, '通达信没在运行（TQ 接口要求客户端已登录）'))
    d = client.get('/api/tdx/status').json()['data']
    assert d['available'] is True and d['client_running'] is False
    assert d['logged_in'] is None and d['background_sync'] is None \
        and d['server_connected'] is None and d['tq_busy'] is None
    assert '恒为空' in d['not_probed']
    assert d['tq_ready'] is False and '客户端已登录' in d['tq_reason']
    assert d['vipdoc_dir'].endswith('vipdoc')


def test_status_探测本身坏了不写成没在跑(client, monkeypatch):
    from app.tdx_client import watchman
    def boom():
        raise OSError('桌面上枚举失败')
    monkeypatch.setattr(watchman, 'probe', boom)
    d = client.get('/api/tdx/status').json()['data']
    assert d['available'] is False and '没探到' in d['reason'] and '不是「没在跑」' in d['reason']


# ------------- 收盘拉起时点 / 只在交易日 / 条数完整性偏离值（2026-10-07 用户要的两格）

def _chain_at(home, monkeypatch, minute=0, trade=True, lead=None, only_trade=None):
    """把 `chain_status` 的三个外部条件钉住：宿主线程在、日历开不开、配置里的两格。"""
    import datetime as real_dt
    from duanxian import jobs as jstore
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: True)
    monkeypatch.setattr(jobsboard._pg, "trade_day_open", lambda d: trade)
    monkeypatch.setattr(jobsboard, "_live_loops", lambda: (["daily-close", "e1-fill-all"], []))
    if lead is not None:
        jstore.set_setting("daily_close", via="test", launch_lead_min=lead)
    if only_trade is not None:
        jstore.set_setting("daily_close", via="test", trading_days_only=only_trade)
    return jobsboard.chain_status(now=real_dt.datetime(2026, 10, 8, 15, minute))


def test_拉起时刻由后端算好_不许让人自己减(client, home, monkeypatch):
    """页面上只该看见"15:49 拉起、15:59 开链"。让人自己从 15:59 减 10 分钟，
    减错了没有任何地方会发现 —— 所以算好的时刻必须一起交出去。"""
    st = _chain_at(home, monkeypatch, lead=10)
    row = next(s for s in st["stages"] if s["key"] == "daily_close")
    assert row["launch_lead_min"] == 10 and row["launch_at"] == "15:49"
    assert row["run_at"] == "15:59" and row["trading_days_only"] is True
    # 不提前时给的就是链的时刻本身，不是空（空会被读成"没配"）
    plain = _chain_at(home, monkeypatch, lead=0)
    zero = next(s for s in plain["stages"] if s["key"] == "daily_close")
    assert zero["launch_lead_min"] == 0 and zero["launch_at"] == zero["run_at"]


def test_只在交易日关掉后_节假日那次必须自己说会跑(client, home, monkeypatch):
    """这一格唯一的价值就是"日历说不是交易日，我仍然发一次"。
    但 `is_trade_day` 照实交 False —— 关掉它不等于那天真的有数。"""
    off = _chain_at(home, monkeypatch, trade=False, only_trade=False)
    assert off["is_trade_day"] is False and off["today_will_run"] is True
    on = _chain_at(home, monkeypatch, trade=False, only_trade=True)
    assert on["today_will_run"] is False


def test_提前量非法整笔不写并端回原因(client):
    from duanxian import jobs as jstore
    code, resp = jobsboard.set_knob("daily_close", "launch_lead_min", 500, via="test")
    assert code == 400 and "0~240" in resp["reason"]
    merged, _ = jstore.settings("daily_close", jstore.DAILY_CLOSE_DEFAULTS)
    assert merged["launch_lead_min"] == 0
    assert jobsboard.set_knob("daily_close", "launch_lead_min", 0, via="test")[0] == 200
    assert jobsboard.set_knob("nope", "launch_lead_min", 5, via="test")[0] == 404


def test_已有的三件各有自己的路不许从这一格改(client):
    """`enabled/run_at/note` 早就各有一条写入面（各自的代价说明也不同）。
    这里放开就等于同一个状态两个入口、留痕两种说法。"""
    for field in ("enabled", "run_at", "note"):
        code, resp = jobsboard.set_knob("daily_close", field, "x", via="test")
        assert code == 400 and "请走它自己的那条路" in resp["reason"]


def test_链面板把偏离值与出处一起交出去(client, home, monkeypatch):
    """判"全不全"决定收盘链要不要再下一遍，而阈值现在是人在页面上改的条数。
    改了不显示 = 下一次误判时没人知道按哪个数判的。"""
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: True)
    monkeypatch.setattr(jobsboard._pg, "trade_day_open", lambda d: True)
    monkeypatch.setattr(jobsboard, "_live_loops", lambda: (["daily-close"], []))
    st = jobsboard.chain_status()
    comp = st["completeness"]
    assert comp["available"] is True and comp["deviation"] == 30
    assert comp["deviation_minute"] == 200 and "偏离值" in comp["rule"]
    assert "条数" in comp["rule"] and "**" not in comp["rule"], "页面是纯文本，markdown 星号会原样显示"
    assert comp["kinds"], "一个口径都没列 = 这格是空壳"
    # 日历覆盖到哪天必须交出来：文案里那句"只登到 2026-12-31"若写死，明年就是假话
    assert "calendar_through" in st and (st["calendar_through"] is None
                                         or re.fullmatch(r"\d{4}-\d{2}-\d{2}", st["calendar_through"]))
    for row in comp["kinds"]:
        assert row["deviation"] in (30, 200), row["kind"]
        assert row["deviation_from"], row["kind"]


def test_链面板说出今天这个交易日结论是哪一层给的(client, home, monkeypatch):
    """2027 年起日历排不住，判据还剩两层（星期几、行情时间戳）。页面必须交出
    **依据哪一层**，否则"日历说不是交易日"与"日历根本没有这一天"在界面上长成同一个字，
    前者不用管、后者要人年底去接下一年的排班。余量天数同理：只剩一个月得先响。
    """
    monkeypatch.setattr(jobsboard._pg, "enabled", lambda *a, **k: True)
    monkeypatch.setattr(jobsboard._pg, "trade_day_open", lambda d, now=None: None)
    monkeypatch.setattr(jobsboard._pg, "trade_day_evidence", lambda d, now=None: "quote")
    monkeypatch.setattr(jobsboard._pg, "calendar_coverage",
                        lambda: {"max_date": "2026-12-31", "days_left": 84,
                                 "running_out": False})
    monkeypatch.setattr(jobsboard, "_live_loops", lambda: (["daily-close"], []))
    st = jobsboard.chain_status()
    assert st["is_trade_day"] is None and st["trade_day_evidence"] == "quote"
    assert st["max_date"] == "2026-12-31" and st["days_left"] == 84
    assert st["running_out"] is False


def test_页面改偏离值_一次一件_非法不写(client, home):
    code, resp = jobsboard.set_completeness("test", deviation=15)
    assert code == 200 and "deviation=15" in resp["reason"]
    assert jobsboard.completeness_status()["deviation"] == 15
    assert "系统配置" in next(r for r in jobsboard.completeness_status()["kinds"]
                            if r["kind"] == "stock_daily")["deviation_from"]
    # 两件一起提交：拒（与开关/时点同一套规矩）
    assert jobsboard.set_completeness("test", deviation=5, deviation_minute=5)[0] == 400
    assert jobsboard.completeness_status()["deviation"] == 15
    # 非法值同样整笔不写
    assert jobsboard.set_completeness("test", deviation=-1)[0] == 400
    assert jobsboard.completeness_status()["deviation"] == 15
    # 空请求不是"改成空"，是没人说改什么
    assert jobsboard.set_completeness("test")[0] == 400


def test_端点读写走的是同一份配置(client):
    got = client.get("/api/jobs/completeness").json()["data"]
    assert got["deviation"] == 30 and got["deviation_minute"] == 200
    r = client.post("/api/jobs/completeness", json={"deviation": 45}).json()
    assert r["data"]["deviation"] == 45
    assert client.post("/api/jobs/completeness", json={"deviation": 99999999}).status_code == 400
    # 改完必须能在链面板那一格里看到同一个数（两处读的是同一份，不是各存一份）
    st = client.get("/api/jobs/chain").json()["data"]
    assert st["completeness"]["deviation"] == 45
    # 用完交还默认，别把真配置留在测试值上
    assert client.post("/api/jobs/completeness", json={"deviation": 30}).status_code == 200


def test_页面改拉起时点走的是同一条面(client):
    r = client.post("/api/jobs/chain", json={"key": "daily_close", "launch_lead_min": 12})
    assert r.status_code == 200, r.text
    row = next(s for s in r.json()["data"]["stages"] if s["key"] == "daily_close")
    assert row["launch_lead_min"] == 12 and row["launch_at"] == "15:47"
    r2 = client.post("/api/jobs/chain", json={"key": "daily_close", "trading_days_only": False})
    assert r2.status_code == 200
    assert next(s for s in r2.json()["data"]["stages"]
                if s["key"] == "daily_close")["trading_days_only"] is False
    # 收尾：交还默认（0 / true），否则今天的 15:59 会按测试值跑
    client.post("/api/jobs/chain", json={"key": "daily_close", "launch_lead_min": 0})
    client.post("/api/jobs/chain", json={"key": "daily_close", "trading_days_only": True})


def test_按开关停用的循环不算缺勤(home, monkeypatch):
    """10-08 加第九条（同花顺云端同步，默认关）之后必须有的读法：
    "缺"= 该活着却没有；被人（或默认值）明确关掉的另列一格。
    混在一起的话，缺勤表会永久红着，真缺的那一次反而看不出来。"""
    import threading
    import datetime as real_dt
    now = real_dt.datetime(2026, 10, 8, 12, 0, 0, tzinfo=real_dt.timezone(
        real_dt.timedelta(hours=8)))
    # 假装除了 ths-cloud-sync 之外所有线程都在
    names = set(jobsboard.HOST_LOOPS) - {'ths-cloud-sync'}
    monkeypatch.setattr(threading, 'enumerate',
                        lambda: [type('T', (), {'name': n})() for n in names])
    data = jobsboard.loop_switches(now)
    assert 'ths-cloud-sync' not in data['missing_loops'], data['missing_loops']
    assert 'ths-cloud-sync' in data['switched_off_loops'], data['switched_off_loops']
    # 而真缺的那一条仍然要报出来
    monkeypatch.setattr(threading, 'enumerate',
                        lambda: [type('T', (), {'name': n})() for n in
                                 (set(jobsboard.HOST_LOOPS) - {'daily-close'})])
    again = jobsboard.loop_switches(now)
    assert 'daily-close' in again['missing_loops'], '把"缺"和"关"分开不许把真缺的也一起吞掉'


# ------------------------------------------------ 加工段逐步台账 + 单步补跑（2026-10-08）
# 用户点名要的两件事：① 失败要看得见（哪一步、为什么、多久），② 那一步能手动补一次。
# 这里钉的是**契约**：读不到台账要说"读不到"、补跑要与链抢同一把锁、
# 而"页面上点一下"这条路径在测试进程里绝不能起真子进程。

class _FakeE1:
    STAGES = [("daily", "日线入库", True), ("fundflow", "大盘资金流", False)]

    def __init__(self, ledger):
        self._ledger = ledger
        self.timeouts = []

    def read_ledger(self):
        return self._ledger

    def step_timeout(self, name):
        self.timeouts.append(name)
        return 42


def test_没有台账时说读不到而不是说没有失败(home, monkeypatch):
    """空台账 ≠ 全绿。这一步骗人的话，页面会在"从没跑过"的时候显示"上一次全部成功"。"""
    monkeypatch.setattr(jobsboard, "_e1", lambda: _FakeE1({}))
    st = jobsboard.chain_steps()
    assert st["available"] is False and "还没有台账" in st["reason"]
    assert st["steps"] == [] and st["failed_steps"] == []
    assert len(st["rerunnable"]) == 2, "能不能补跑按清单答，跟台账在不在没关系"


def test_台账逐步交出结果并把没成的列出来(home, monkeypatch):
    monkeypatch.setattr(jobsboard, "_e1", lambda: _FakeE1({
        "target": "2026-09-30", "verdict": "PARTIAL", "line": "追平：1 步成功，1 步失败",
        "written_at": "2026-10-08T14:30:50+08:00", "lagging_tables": ["大盘资金流"],
        "steps": [{"name": "daily", "label": "日线入库", "core": True, "rc": 0,
                   "seconds": 12.5, "rounds": 1, "module": "ingest.jobs.c10_catchup",
                   "status": "ok", "reason": None, "tail": None},
                  {"name": "fundflow", "label": "大盘资金流", "core": False, "rc": 3,
                   "seconds": 4.0, "rounds": 2, "module": "ingest.jobs.c21_fundflow",
                   "status": "fail", "reason": "按口径拒写：今天不是交易日", "tail": "rc=3"}]}))
    st = jobsboard.chain_steps()
    assert st["available"] is True and st["verdict"] == "PARTIAL"
    assert st["failed_steps"] == ["fundflow"]
    assert st["lagging_tables"] == ["大盘资金流"]
    assert [s["status"] for s in st["steps"]] == ["ok", "fail"]
    # 原因与输出末尾必须原样带着：只给一个步骤名分不清"网络抖"与"数据缺"
    assert "按口径拒写" in st["steps"][1]["reason"] and st["steps"][1]["tail"] == "rc=3"


def test_补跑一步的三种拒绝各说各的(home, monkeypatch):
    """404 名字不认识 / 409 锁被链占着 / 503 连清单都读不到 —— 三种都得原样带出原因，
    否则点了按钮等于什么都没发生，而界面看着像成功。"""
    monkeypatch.setattr(jobsboard, "_e1", lambda: _FakeE1({}))
    code, body = jobsboard.rerun_step("nope")
    assert code == 404 and "没有这一步" in body["reason"]
    monkeypatch.setattr(jobsboard, "_e1", lambda: None)
    assert jobsboard.rerun_step("daily")[0] == 503
    monkeypatch.setattr(jobsboard, "_e1", lambda: _FakeE1({}))
    monkeypatch.setattr(jobsboard.jstore, "claim", lambda key: False)
    code, body = jobsboard.rerun_step("daily")
    assert code == 409 and "本次未触发" in body["reason"]


def test_补跑成功只排队并且真进程在测试里绝不起来(home, monkeypatch):
    """这条路径会 `subprocess.run` 起一个真作业 —— 测试里绝不能真起（同 `c23.spawn` 的洞）。
    钉两层：`_spawn_step` 在 pytest 进程里直接返回 0；`rerun_step` 只回 202 并说清约一分钟后刷新。"""
    monkeypatch.setattr(jobsboard, "_e1", lambda: _FakeE1({}))
    monkeypatch.setattr(jobsboard.jstore, "claim", lambda key: True)
    assert jobsboard._spawn_step("daily", 42) == 0, "测试进程里那颗按钮不许真起子进程"
    code, body = jobsboard.rerun_step("daily")
    assert code == 202 and body["status"] == "queued" and "台账会更新" in body["reason"]


# ------------------------------------------------ 运行流水要一路走到界面（10-10 新增）

def test_台账里的runs要出现在作业行的run_history上(home):
    """链路是三跳：`jobs.record()` 写 → `state.json` → `jobsboard` 读出 → 前端 `HistoryBlock` 列出来。
    以前常驻循环只留"最近一次"，页面上那句"跑了而没留历史"就是这一跳断了。
    这里不测前端渲染，测的是**中间那跳有没有把东西带出来**（键名对不对）。"""
    from duanxian import jobs as jstore

    jstore.record("daily_close", ok=False, detail={"reason": "数据没到位（vipdoc 与 TQ 都没到）"})
    jstore.record("daily_close", ok=True, detail={"written": 40})
    jstore.record("daily_close", ok=False, detail={"skipped": True, "reason": "按日历今天不开市"})
    row = jobsboard._recorded_job("daily_close", "收盘即时链", "15:59", 600, ["market_data.*"])
    hist = row["run_history"]
    assert [(h["outcome"], h.get("error")) for h in hist][:2] == [
        ("fail", "数据没到位（vipdoc 与 TQ 都没到）"), ("ok", None)], hist
    assert hist[1]["rows_written"] == 40, "成功那笔的行数要带着（前端按 number 才显示）"
    assert hist[2]["outcome"] == "skipped", "按口径跳过也要在流水里，不能等于没跑过"
    assert all(re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$", h["started_at"]) for h in hist), \
        "时刻的格式必须与页面/其它台账一致（同一屏两种时间写法会被读成两种精度）"
