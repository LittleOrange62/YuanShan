"""2026-10-07 用户要的两处可配置的回归：

① 「你就按照前一天的数量 偏离值在 30 以内就算全了。（这个偏离值可以在系统配置中手动设置和修改。）」
② 「收盘自己拉起的自动执行时间点，要做成可以配置的……（可以选择是否只在交易日执行……）」

守的四件事：
1. **默认值只住 `duanxian/jobs.py`**：`completeness.py` 与页面都读同一份，谁也不许再写一个 30；
2. **0 必须填得进去**（偏离值 0 = 少一条都算不全、提前量 0 = 不提前拉起），
   这两档恰是用户最想试的，按"正整数"统一判就等于把他自己的要求堵死；
3. **上限防手滑**：偏离值填成 30000 等于取消判据，提前 240 分钟以上拉起没有意义；
4. **一个键非法就整笔不写**，否则页面显示的数字与实际跑的不是同一份（半改完最难查）。
"""

from __future__ import annotations

import pytest

from duanxian import jobs


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """配置必须落在测试自己的目录：否则会用真 `~\\.duanxian-agents` 里的值判，
    绿灯与代码无关，还会把真配置写脏（这条作业配置是持久状态，默认值救不回来）。"""
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(tmp_path / "datahome"))
    return tmp_path / "datahome"


# ---- ① 偏离值 ---------------------------------------------------------------

def test_偏离值默认_日线30_分钟200_且只住一处():
    assert jobs.COMPLETENESS_DEFAULTS == {
        "deviation": 30, "deviation_minute": 200, "note": ""}
    # 判据那一侧不许有第二份数字：`deviation_for` 读的就是这里，来源串要能说清没被改过
    from duanxian import completeness
    val, where = completeness.deviation_for("stock_daily")
    assert val == 30 and where == "默认（系统配置里没改过这一项）"
    assert completeness.deviation_for("vipdoc_lc1")[0] == 200


def test_偏离值取0合法_那是最严的一档():
    jobs.set_setting("completeness", via="cli:test", deviation=0)
    merged, warns = jobs.settings("completeness", jobs.COMPLETENESS_DEFAULTS)
    assert merged["deviation"] == 0 and warns == []
    from duanxian import completeness
    val, where = completeness.deviation_for("stock_daily")
    assert val == 0 and where == "系统配置 completeness.deviation"


def test_偏离值超限整条拒写并说清上限():
    why, resp = jobs.set_setting("completeness", via="cli:test", deviation=100001)
    assert why and "0~100000" in why
    merged, _ = jobs.settings("completeness", jobs.COMPLETENESS_DEFAULTS)
    assert merged["deviation"] == 30                     # 没落盘，仍按原值


def test_分钟类与日线各改各的互不覆盖():
    jobs.set_setting("completeness", via="cli:test", deviation=15)
    jobs.set_setting("completeness", via="cli:test", deviation_minute=300)
    merged, _ = jobs.settings("completeness", jobs.COMPLETENESS_DEFAULTS)
    assert merged["deviation"] == 15 and merged["deviation_minute"] == 300
    from duanxian import completeness
    assert completeness.deviation_for("stock_daily")[0] == 15
    assert completeness.deviation_for("stock_minute_1m")[0] == 300
    assert completeness.deviation_for("vipdoc_day")[1] == "系统配置 completeness.deviation"


# ---- ② 拉起时点与"只在交易日执行" -------------------------------------------

def test_拉起提前量与交易日开关在默认表里():
    d = jobs.DAILY_CLOSE_DEFAULTS
    assert d["launch_lead_min"] == 0 and d["trading_days_only"] is True
    assert jobs.CLOSE_DEFAULTS["daily_close"] is d        # 同一份对象，不是抄了一份


def test_提前量0与240边界内可写_超出拒():
    assert jobs.valid("launch_lead_min", 0) is None
    assert jobs.valid("launch_lead_min", 240) is None
    assert jobs.valid("launch_lead_min", 241) is not None
    assert jobs.valid("launch_lead_min", "早一点") is not None


def test_只在交易日这一键只认真假():
    assert jobs.valid("trading_days_only", False) is None
    assert jobs.valid("trading_days_only", "false") is not None


def test_一个键非法就整笔不写():
    """`launch_lead_min=500` 与 `trading_days_only=false` 一起提交时，
    只写进去后半句会让页面上显示的数字与实际跑的不是同一份。"""
    why, _resp = jobs.set_setting("daily_close", via="cli:test",
                                  launch_lead_min=500, trading_days_only=False)
    assert why and "整条没写入" in why
    merged, _ = jobs.settings("daily_close", jobs.DAILY_CLOSE_DEFAULTS)
    assert merged["launch_lead_min"] == 0 and merged["trading_days_only"] is True


def test_提前量落盘后宿主读到的就是它():
    jobs.set_setting("daily_close", via="cli:test", launch_lead_min=10)
    merged, warns = jobs.settings("daily_close", jobs.DAILY_CLOSE_DEFAULTS)
    assert merged["launch_lead_min"] == 10 and warns == []
    note = jobs.change_note("daily_close")
    assert note["changed_by"] == "cli:test"          # 每次写都盖章，不然只能互相猜


def test_值留空等于交还默认():
    jobs.set_setting("daily_close", via="cli:test", launch_lead_min=10)
    jobs.set_setting("daily_close", via="cli:test", launch_lead_min="")
    merged, _ = jobs.settings("daily_close", jobs.DAILY_CLOSE_DEFAULTS)
    assert merged["launch_lead_min"] == 0


def test_不存在的键点名可用的键():
    why, _ = jobs.set_setting("daily_close", via="cli:test", launch_lead_minute=10)
    assert why and "launch_lead_min" in why


def test_收网时长不许被当成可以填0的键():
    """`sync_reap_after_min=0` 的字面意思是"刚起就收进程"，会把正在下的那批打断，
    所以它不在 `ZERO_OK_KEYS` 里 —— 这一条钉住"0 只给那三个有明确 0 语义的键放开"。"""
    assert "sync_reap_after_min" not in jobs.ZERO_OK_KEYS
    assert jobs.valid("sync_reap_after_min", 0) is not None


# ---- ③ 自选同步模式（全量 / 增量）与字符串键的取值范围 ------------------------

def test_同步模式只认两个值_拼错整笔不写():
    """`sync_mode=fulll` 若被照单全收，代码里 `== "append"` 不成立 → 静默按全量跑，
    而页面上那个人以为开了增量。字符串键以前一律放行，这次补上枚举。"""
    assert jobs.valid("sync_mode", "full") is None
    assert jobs.valid("sync_mode", "APPEND") is None      # 大小写不敏感，存下来统一小写
    why = jobs.valid("sync_mode", "fulll")
    assert why and "full/append" in why
    code = jobs.set_setting("watchlist", via="cli:test", sync_mode="fulll")
    assert code[0] and "整条没写入" in code[0]
    merged, _ = jobs.settings("watchlist", jobs.WATCHLIST_DEFAULTS)
    assert merged["sync_mode"] == "full"


def test_同步模式落盘后同步读到的就是它():
    jobs.set_setting("watchlist", via="cli:test", sync_mode="append")
    merged, warns = jobs.settings("watchlist", jobs.WATCHLIST_DEFAULTS)
    assert merged["sync_mode"] == "append" and warns == []
    from duanxian import watchlist_sync as ws
    import inspect
    assert "mode" in inspect.signature(ws.build_plan).parameters


def test_默认表里三个入口共用的在册键都齐了():
    """`intraday` / `watchtower` 10-07 补进来：它们在跑、在记账，但既不在面板清单里
    也没有开关（宿主的代码读不到那个框）。在册之后才有"看得见 = 关得掉"这回事。"""
    assert set(jobs.CLOSE_DEFAULTS) >= {"daily_close", "fill_all", "fundflow", "watchlist",
                                        "coverage", "tdx_watch", "intraday", "watchtower"}
    for key in ("intraday", "watchtower"):
        assert jobs.CLOSE_DEFAULTS[key] == {"enabled": True, "note": ""}, key
        assert "run_at" not in jobs.CLOSE_DEFAULTS[key], key   # 槽位型/间隔型都不该有时点框
    # 快讯的间隔只有一个真源：`newswire.POLL_SECONDS`，jobs 只是把它整理成表
    from duanxian import newswire as nw
    assert jobs.news_defaults()["interval_seconds"] == nw.POLL_SECONDS


# ---- ③ 常驻循环的开关读法与"跳过"记账（2026-10-08 逐条实跑后补） -------------

def test_循环开关的读法全仓只有一份():
    """10-07 那条"页面关了、线程照跑的假开关"修完时，读法仍然有**四份拷贝**：
    `server._loop_enabled`、`_watchlist_enabled`、`_fundflow_enabled`、`vr/watchtower.enabled`。
    四份的含义是"改一处不传另三处"——下一个人加总闸只会加进他看到的那一份。
    现在只许 `duanxian/jobs.loop_enabled` 一份，其余全部转发。"""
    import inspect
    import server
    from vr import watchtower as wt

    for fn in (server._loop_enabled, server._watchlist_enabled,
               server._fundflow_enabled, server._coverage_enabled,
               server._tdx_watch_enabled, wt.enabled):
        src = inspect.getsource(fn)
        assert "loop_enabled(" in src, f'{fn.__qualname__} 没有转发到 jobs.loop_enabled'
    # 环境变量三态列表不许在别处再写一遍（那是"两份读法"最典型的复发形状）
    for fn in (server._watchlist_enabled, server._fundflow_enabled, wt.enabled):
        assert '"off"' not in inspect.getsource(fn), f'{fn.__qualname__} 里还留着自己的总闸读法'


def test_pytest_进程里起不了真循环线程():
    """洞只留一个，而且必须留在**起线程**那一层。

    `watchtower` 的读法里刻意**不带** pytest 硬挡（`pytest_block=False`）：既有三条测试要直接
    调 `_loop()` 喂假行情，验的是坏轮怎么出声、怎么记账。真危险是"测试里冒出一条每 3 秒打
    腾讯行情的线程"，那由 `ensure_started()` 自己的 PYTEST 硬挡挡住。
    """
    import server
    from vr import watchtower as wt
    assert jobs.loop_enabled("VIBE_NEWS", "news", jobs.news_defaults()) is False
    assert server._fundflow_enabled() is False and server._watchlist_enabled() is False
    # 这一条按新读法是真会答"该干活"的（测试喂假行情要走到轮子里）
    assert wt.enabled() is True
    before = wt._thread
    wt.ensure_started()
    assert wt._thread is before, 'ensure_started 在测试进程里起了真线程 —— 每 3 秒打外部行情'


def test_按口径跳过不涨连败也不假装成功(home):
    """休市两天里 `intraday` 会连着一堆"未开市不抓快照"。旧记账把它算成失败，
    于是连败能刷到几十上百，真故障混在里面看不出来；把它算成成功更糟——
    那一轮什么都没抓。第三态：状态写 skipped，连败不动，`last_ok_at` 不动。"""
    jobs.record("intraday", ok=False, detail={"reason": "上一次真失败"})
    base = jobs.state()["intraday"]
    assert base["last_status"] == "fail" and base["consecutive_failures"] == 1

    got = jobs.record("intraday", ok=False,
                      detail={"reason": "2026-10-07 未开市，不抓快照", "skipped": True})
    assert got["last_status"] == "skipped", "跳过被记成了失败或成功，面板上就分不出噪音与故障"
    assert got["consecutive_failures"] == 1, "跳过不该推进连败计数"
    assert got["last_ok_at"] == base["last_ok_at"], "跳过没有成功证据，不许刷新最近成功"
    assert got["last_error"] and "未开市" in got["last_error"], "跳过的原因必须留在面板上，否则像静默漏跑"

    ok = jobs.record("intraday", ok=True, detail={"rows": 5400})
    assert ok["last_status"] == "ok" and ok["consecutive_failures"] == 0 and ok["last_ok_at"]


def test_盘中快照的两条按口径拒抓都是跳过而不是失败(home):
    """`intraday.capture()` 里有两条"这一轮本来就不该抓"：历史日补不回来、今天休市。
    它们必须带 `skipped` 标记，宿主才敢把它们与"源没更新、超时"分开——后者该重试也该红。

    这里查源码而不是执行：休市与否取决于墙钟与日历，用执行来断言会做出一条
    每天有一半时间说谎的测试。
    """
    import inspect
    from duanxian import intraday
    src = ' '.join(inspect.getsource(intraday.capture).split())
    for why in ('不抓快照', '历史日补不回来'):
        at = src.rfind(why)      # 第一次命中在 docstring 里，取最后一次（真正的 return 那一处）
        assert at >= 0, f'找不到"{why}"这条按口径拒抓的出口'
        window = src[max(0, at - 160):at + 60]
        assert '"skipped": True' in window, f'"{why}" 那条没带 skipped 标记，会被记成失败：{window[:120]}'
        assert '"ok": False' in window, f'"{why}" 不该被标成成功：{window[:120]}'


# ---- ④ 第九条循环：同花顺云端自选同步（用户 2026-10-08 点名） ----------------

def test_频率档只有两种且每周只认勾选的那几天(home):
    """用户要的是「可以选择每周、每日这样的选项，时间也可以修改」——
    判据必须只有一份（`jobs.freq_allows`），否则宿主按一种跑、页面按另一种显示。"""
    import datetime as dt
    ok = {"run_freq": "daily", "weekly_days": ""}
    for d in (dt.date(2026, 10, 8), dt.date(2026, 10, 11)):      # 周四、周日
        assert jobs.freq_allows(ok, d) is True
    wed_fri = {"run_freq": "weekly", "weekly_days": "2,4"}       # 周三、周五
    assert jobs.freq_allows(wed_fri, dt.date(2026, 10, 7)) is True       # 周三
    assert jobs.freq_allows(wed_fri, dt.date(2026, 10, 8)) is False      # 周四不跑
    # 写坏不许让循环静默停产：判不了就照跑，由 settings() 的 warns 出声
    assert jobs.freq_allows({"run_freq": "weekly", "weekly_days": "八"}, dt.date(2026, 10, 8)) is True
    assert jobs.freq_allows({"run_freq": "每天", "weekly_days": ""}, dt.date(2026, 10, 8)) is True
    jobs.set_setting("ths_sync", via="cli:test", run_freq="weekly", weekly_days="1,3")
    merged, warns = jobs.settings("ths_sync", jobs.THS_SYNC_DEFAULTS)
    assert merged["run_freq"] == "weekly" and merged["weekly_days"] == "1,3" and warns == []
    jobs.set_setting("ths_sync", via="cli:test", run_freq="monthly")     # 非法值
    merged2, warns2 = jobs.settings("ths_sync", jobs.THS_SYNC_DEFAULTS)
    assert merged2["run_freq"] == "weekly", f"非法值被采纳了：{merged2}"


def test_同花顺那条默认关且参数都在册(home):
    """它动的是桌面上的客户端窗口 —— 第一次必须人在键盘前演练过才开，所以**默认 False**；
    一旦哪天有人把默认改成 True，这条会红，逼他重新确认一次。"""
    assert jobs.THS_SYNC_DEFAULTS["enabled"] is False
    assert jobs.THS_SYNC_DEFAULTS["run_at"] == "15:30", "15:59 收盘链之前才拉得及（20:40 读的就是这份 ini）"
    assert "ths_sync" in jobs.CLOSE_DEFAULTS
    from vr import jobsboard
    keys = {k for _n, k, _l, _o in jobsboard.LOOP_JOBS}
    assert "ths_sync" in keys, "在册表里没有它 —— 看不见也就关不掉（10-07 那条老坑）"
    row = [r for r in jobsboard.loop_switches()["loops"] if r["key"] == "ths_sync"][0]
    assert row["run_freq_choices"] and row["weekday_choices"], "频率与每周哪几天必须有出处，页面不自己编"
    assert row["enabled"] is False


def test_同花顺流程不许按镜像名杀进程而且测试里有洞(home):
    """误伤判据：`taskkill /IM hexin.exe` 会把用户正开着的同一个客户端一起带走
    （通达信那条链 09-30 实测过这个代价）。只允许按 pid 收，且测试进程里硬挡真起。"""
    import inspect
    from ingest.jobs import c23_ths_cloud_sync as ths
    src = inspect.getsource(ths)
    # 只查真调用：docstring 里写"绝不按镜像名杀（/IM）"是纪律说明，不是犯禁
    calls = [ln for ln in src.splitlines() if 'subprocess.run' in ln or 'Popen(' in ln]
    assert calls and not any('/IM' in ln for ln in calls), f'真按镜像名杀了：{calls}'
    assert "'/PID'" in src, '收窗口要按本次起的 pid 收'
    assert 'PYTEST_CURRENT_TEST' in src, '没有测试硬挡：单测里会真起同花顺窗口'
    assert 'LOGIN_MARKS' in src and ths.looks_like_login_wall(['同花顺 网上行情系统']) is None
    assert ths.looks_like_login_wall(['同花顺登录']) == '同花顺登录'
    # 只探测模式必须一个字都不动界面
    res = ths.run_once(check_only=True)
    assert res['ok'] and res['launched'] is None and res['already_running'] == ths.running_pids()
