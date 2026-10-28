"""自动执行机制的"看得见"那一层：体检项与补跑钩子的回归。

这些项存在的唯一理由，是 09-28/29/30 连续三天出现同一类事：**机制在跑、页面也绿，
但那天该做的事没做**（收盘链在 16:31 判 stop 而通达信 17:22 才落盘；自选同步作业
从未成功却在页面上显示 41 个旧组；`c10` 用参考仓库路径起下载，每步 `can't open file`
而体检的「外部路径引用」是绿的）。所以每条判据都要回答两件事：
① 该报的时候报得出来；② **不该报的时候别报**（一条常年误报的体检项等于没有体检项）。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

from scripts import manage

# `vr/jobsboard.py` 的面板读数与开关生效时机是同一件事的两端，所以放同一个文件里钉。
# ⚠️ `sys.path` 只能**追加**：插到最前会让 `vr/app.py` 抢走顶层 `app/` 包
# （`ModuleNotFoundError: 'app' is not a package`，10-06 真踩过，同批红了别人 22 条）。
import sys as _sys
_sys.path.append(str(Path(__file__).resolve().parents[1] / "vr"))
import jobsboard  # noqa: E402


# ---------------------------------------------------------------- 常驻循环完整性
def _write_loop_pairs(root: Path, pairs: tuple[tuple[str, str], ...],
                      on_demand: tuple[str, ...] = ()) -> None:
    """写真形状：`vr/jobsboard.py::LOOP_JOBS` 是 `(线程名, 记账键, 中文名, 关掉的代价)`。"""
    (root / "vr").mkdir(parents=True, exist_ok=True)
    body = ("LOOP_JOBS = (\n"
            + "".join(f'    ("{n}", "{k}", "{n} 的中文名", "关掉会失去什么"),\n' for n, k in pairs)
            + ")\nHOST_LOOPS = tuple(name for name, _k, _l, _o in LOOP_JOBS)\n")
    if on_demand:
        body += "ON_DEMAND_LOOPS = (" + "".join(f'"{n}", ' for n in on_demand) + ")\n"
    (root / "vr" / "jobsboard.py").write_text(body, encoding="utf-8")


def _write_loops(root: Path, names: tuple[str, ...]) -> None:
    """旧形状（只有名字清单）——留着它才能证明兜底路径还在说话。"""
    (root / "vr").mkdir(parents=True, exist_ok=True)
    body = "HOST_LOOPS = (" + "".join(f'"{n}", ' for n in names) + ")\n"
    (root / "vr" / "jobsboard.py").write_text(body, encoding="utf-8")


def test_循环清单从磁盘读而不是问进程(monkeypatch, tmp_path):
    """进程自己报"一条不缺"是不能信的：它拿的是**编译进去的那份清单**比。
    10-06 实测过这个形状 —— 磁盘上已经声明 7 条（含 `tdx-watch`），
    跑着的服务报"活着 6 条、缺 0 条"。"""
    _write_loop_pairs(tmp_path, (("daily-close", "daily_close"),
                                 ("newswire", "news"), ("tdx-watch", "tdx_watch")))
    got, why = manage.declared_loops(tmp_path)
    assert got == ["daily-close", "newswire", "tdx-watch"] and why is None
    assert manage.declared_loop_jobs(tmp_path)[0] == [("daily-close", "daily_close"),
                                                      ("newswire", "news"), ("tdx-watch", "tdx_watch")]


def test_清单读不出来要报判不了不许当绿灯(monkeypatch, tmp_path):
    """`HOST_LOOPS` 被改名/删掉时，最坏的写法是"没声明 = 不需要跑"→ 这项永久绿。"""
    (tmp_path / "vr").mkdir(parents=True)
    (tmp_path / "vr" / "jobsboard.py").write_text("X = 1\n", encoding="utf-8")
    got, why = manage.declared_loops(tmp_path)
    assert got == [] and "找不到 LOOP_JOBS" in why
    monkeypatch.setattr(manage, "live_loops", lambda port, timeout=8.0: ({"host_loops": [], "pid": 1}, None))
    ok, action = manage.loop_inventory(tmp_path, 8910)
    assert not ok and "判不了" in action


def test_缺一条就点名那条(monkeypatch, tmp_path):
    _write_loops(tmp_path, ("daily-close", "newswire", "fundflow-sina"))
    monkeypatch.setattr(manage, "live_loops", lambda port, timeout=8.0: (
        {"host_loops": ["daily-close", "newswire"], "host_loops_missing": ["fundflow-sina"],
         "pid": 4242}, None))
    ok, action = manage.loop_inventory(tmp_path, 8910)
    assert not ok and "fundflow-sina" in action and "4242" in action
    assert "旧代码" not in action           # 服务自己也知道该有这条 ⇒ 不是旧进程，是没起来


def test_进程跑的是旧代码要单独说一句并给出动作(monkeypatch, tmp_path):
    _write_loops(tmp_path, ("daily-close", "tdx-watch"))
    monkeypatch.setattr(manage, "live_loops", lambda port, timeout=8.0: (
        {"host_loops": ["daily-close"], "host_loops_missing": [], "pid": 99}, None))
    ok, action = manage.loop_inventory(tmp_path, 8910)
    assert not ok and "旧代码" in action and "重启 8910" in action


def test_服务没起就一条循环都没有不许写成正常(monkeypatch, tmp_path):
    _write_loops(tmp_path, ("daily-close",))
    monkeypatch.setattr(manage, "live_loops", lambda port, timeout=8.0: (None, "ConnectionRefusedError"))
    ok, action = manage.loop_inventory(tmp_path, 8910)
    assert not ok and "服务没在跑" in action and "15:59" in action


# ---------------------------------------------------------------- 按需起的循环（watchtower）
def test_按需起清单也从磁盘读一份(monkeypatch, tmp_path):
    _write_loop_pairs(tmp_path, (("daily-close", "daily_close"), ("watchtower", "watchtower")),
                      on_demand=("watchtower",))
    assert manage.declared_on_demand(tmp_path) == ["watchtower"]
    # 文件里没这一行时读成空 ⇒ 退回"该活着却没有就算缺"，方向是多报一条红不是静默放行
    _write_loop_pairs(tmp_path, (("daily-close", "daily_close"),))
    assert manage.declared_on_demand(tmp_path) == []


def test_按需起的那条没起不算缺但要拿台账反证(monkeypatch, tmp_path):
    """10-11 实测的真形状：`watchtower` 是第一次有人订阅盯盘才起线程（`ensure_started()`），
    夜里没人盯盘 ⇒ 服务自报"缺 watchtower"、doctor 天天一条红，而那句
    "这些机制今天不会自己跑"是**假话**。豁免同时必须带反证：台账里连键都没有
    ⇒ 它从没起过，那就不是"按需"而是接线断了（下一条用例）。"""
    _write_loop_pairs(tmp_path, (("daily-close", "daily_close"),
                                 ("newswire", "news"), ("watchtower", "watchtower")),
                      on_demand=("watchtower",))
    monkeypatch.setattr(manage, "live_loops", lambda port, timeout=8.0: (
        {"host_loops": ["daily-close", "newswire"], "host_loops_missing": [], "pid": 7}, None))
    monkeypatch.setattr(manage, "state_keys", lambda: {"daily_close", "news", "watchtower"})
    ok, action = manage.loop_inventory(tmp_path, 8910)
    assert ok and "watchtower" not in action.split("按需起")[0]
    assert "2 条常驻循环都在" in action, "分母不许把按需那条算进「该一直活着」"
    assert "按需起的 watchtower 此刻没起（没人订阅，按设计）" in action


def test_按需起那条一次账都没记过要转红(monkeypatch, tmp_path):
    """不许"按需"变成垃圾桶：线程从没起来过的形状与没订阅的形状在界面上一模一样，
    唯一能分开它们的是台账有没有过一笔账。"""
    _write_loop_pairs(tmp_path, (("daily-close", "daily_close"), ("watchtower", "watchtower")),
                      on_demand=("watchtower",))
    monkeypatch.setattr(manage, "live_loops", lambda port, timeout=8.0: (
        {"host_loops": ["daily-close"], "host_loops_missing": [], "pid": 7}, None))
    monkeypatch.setattr(manage, "state_keys", lambda: {"daily_close"})
    ok, action = manage.loop_inventory(tmp_path, 8910)
    assert not ok and "watchtower" in action and "从没起来过" in action and "ensure_started" in action


# ---------------------------------------------------------------- 采集作业的四种状态
@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "datahome"
    (home / "jobs").mkdir(parents=True)
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    return home


def _state(home, mapping):
    (home / "jobs" / "state.json").write_text(json.dumps(mapping), encoding="utf-8")


def _cfg(home, mapping):
    (home / "jobs" / "config.json").write_text(json.dumps(mapping), encoding="utf-8")


def test_从未成功但反复在跑要判红(home):
    """2026-09-28 的现场：自选同步 `连败 91` 且从未成功，页面照常显示 41 个组（旧存量）。
    旧措辞只写"最近成功 None"，红不红取决于一个阈值比较，人看不出严重程度。"""
    _state(home, {"watchlist": {"last_run_at": "2026-09-28 20:40:00", "last_ok_at": None,
                               "last_status": "fail", "consecutive_failures": 91,
                               "last_error": "null value in column added_at"}})
    ok, action = manage.collector_status("2026-09-28")
    assert not ok and "从未成功过" in action and "连败 91" in action
    assert "added_at" in action                    # 归因必须带出来，不然还得再去翻文件


def test_从没跑过只提示不判红(home):
    """刚接入的作业一次都没跑，不是故障。判红会让第一项天天红。"""
    _state(home, {"newthing": {"last_run_at": None, "last_ok_at": None,
                              "consecutive_failures": 0}})
    ok, action = manage.collector_status("2026-09-28")
    assert ok and "从未运行" in action


def test_在册却没有运行记录必须现身(home):
    """`coverage` 10-06 的现场：循环在 `host_loops` 里活着，`state.json` 里却没有这个键，
    而体检原来**只遍历 state.json 已有的键** —— 于是一个从不记账的作业在这项里根本不存在。
    最省事的"看着有其实没跑"就是不记账，因为它连红都触发不了。"""
    _state(home, {"watchlist": {"last_run_at": "2026-10-05 20:40:00",
                                "last_ok_at": "2026-10-05 20:40:00"}})
    ok, action = manage.collector_status("2026-10-05")
    want, why = manage.declared_jobs()
    assert not why and "coverage" in want          # 清单只住 duanxian/jobs.py::CLOSE_DEFAULTS
    assert "在册但至今没有运行记录" in action and "coverage" in action
    assert ok                                      # 还没到自己那一档不是故障，别造天天红的项


def test_在册清单读不出来要说判不了而不是当都在(home, tmp_path, monkeypatch):
    (tmp_path / "duanxian").mkdir(parents=True, exist_ok=True)
    (tmp_path / "duanxian" / "jobs.py").write_text("# CLOSE_DEFAULTS 被谁改名了\nX = 1\n", encoding="utf-8")
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    want, why = manage.declared_jobs()
    assert want == [] and "CLOSE_DEFAULTS" in why
    _state(home, {"watchlist": {"last_run_at": "x", "last_ok_at": "x"}})
    ok, action = manage.collector_status("2026-10-05")
    assert "判不了" in action and "不等于都在跑" in action


def test_成功过但最近一场没跑要单独报出来(home):
    """"最近成功 09-20" 与 "应到 09-28" 之间的差别就是缺的那几天。"""
    _state(home, {"daily_close": {"last_run_at": "2026-09-28 15:59:10",
                                 "last_ok_at": "2026-09-20 16:02:10",
                                 "consecutive_failures": 0}})
    ok, action = manage.collector_status("2026-09-28")
    assert not ok and "最近成功停在 2026-09-20" in action and "应到 2026-09-28" in action


def test_观望那一档不判红但要说清为什么没动(home):
    """2026-10-12 的现场：收窗循环 `tdx_shell_reap` 被念成"从未成功过、连败 46"，
    而那 46 轮每一轮都是**按设计不动**（收盘链正在跑）。第三种结果（`detail.skipped`）
    既不刷新 last_ok_at 也不涨连败，所以它排在"从未成功"那段之前判，否则假红照样出来。"""
    _state(home, {"tdx_shell_reap": {"last_run_at": "2026-10-12 17:00:58", "last_ok_at": None,
                                     "last_status": "skipped", "consecutive_failures": 0,
                                     "last_error": "收盘链 fill_all 正在跑 → 这一轮不动任何窗口"}})
    ok, action = manage.collector_status("2026-10-12")
    assert ok, action
    assert "从未成功" not in action and "观望" in action and "fill_all" in action


def test_按需起的作业不按该有今天判红(home):
    """盯盘轮询只有第一次有人打开盯盘页才起线程（`watchtower.ensure_started`）。
    没人盯的那些天它**本来就不该有记录** —— 一条天天误报的体检项等于没有体检项。"""
    _state(home, {"watchtower": {"last_run_at": "2026-10-08 00:18:37",
                                 "last_ok_at": "2026-10-08 00:18:37",
                                 "consecutive_failures": 0}})
    ok, action = manage.collector_status("2026-10-12")
    assert ok and "应到 2026-10-12" not in action, action
    assert "按需" in action
    # 但它要是**报错**了（连败），豁免不能把它一起糊掉
    _state(home, {"watchtower": {"last_run_at": "2026-10-12 09:30:00",
                                 "last_ok_at": "2026-10-08 00:18:37",
                                 "last_status": "fail", "consecutive_failures": 2,
                                 "last_error": "上游 500"}})
    ok2, action2 = manage.collector_status("2026-10-12")
    assert not ok2 and "连败 2" in action2 and "上游 500" in action2


def test_不在册的手动探针隔天不再判红(home):
    """`responses_bridge` 这类只在有人点"测一下"时才跑的探针：10-07 点过一次、HTTP 426 没成，
    之后五天没人再碰 —— 原先它会让这一项**天天红**（"从未成功过"那段不看有没有调度、也不看今天跑没跑）。
    天天误报的体检项等于没有体检项；但**今天又触发过还失败**必须照样报红。"""
    _state(home, {"responses_bridge": {"last_run_at": "2026-10-07 16:32:52", "last_ok_at": None,
                                       "last_status": "fail", "consecutive_failures": 3,
                                       "last_error": "上游返回 HTTP 426"}})
    ok, action = manage.collector_status("2026-10-12")
    assert ok and "不判红" in action and "HTTP 426" in action, action
    _state(home, {"responses_bridge": {"last_run_at": "2026-10-12 10:00:00", "last_ok_at": None,
                                       "last_status": "fail", "consecutive_failures": 1,
                                       "last_error": "上游返回 HTTP 426"}})
    ok2, action2 = manage.collector_status("2026-10-12")
    assert not ok2 and "从未成功过" in action2, action2


def test_停用且过期不算红但必须标出停用(home):
    """人关掉的作业不该被当成故障（那是《收盘链自动开关》那项的职责），
    但也不能悄悄显示成正常 —— 页面上要看得见"这个是关的"。"""
    _state(home, {"daily_close": {"last_run_at": "2026-09-20 15:59:10",
                                 "last_ok_at": "2026-09-20 16:02:10",
                                 "consecutive_failures": 0}})
    _cfg(home, {"daily_close": {"enabled": False}})
    ok, action = manage.collector_status("2026-09-28")
    assert ok and "停用" in action


def test_判不了应跑日时不许据此报缺(home):
    """`expected=None` 是"库里读不到最近一场"，此时只能判连败，
    不能把所有作业都算成缺 —— 那会让 DB 抖动表现为"采集全线故障"。"""
    _state(home, {"daily_close": {"last_run_at": "2026-09-20 15:59:10",
                                 "last_ok_at": "2026-09-20 16:02:10",
                                 "consecutive_failures": 0}})
    ok, action = manage.collector_status(None)
    assert ok and "2026-09-20" in action


# ---------------------------------------------------------------- 作业路径完整性
def test_登记的作业模块不存在必须命中(tmp_path):
    """c10 那一类：`scripts/ops/p0_tdx_predownload.py` 是参考仓库的结构，
    本项目没有这个文件，每步 `can't open file`，退码 2 看着像"下载没成"。"""
    jobs = tmp_path / "ingest" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "c1_day.py").write_text("", encoding="utf-8")
    (jobs / "x.py").write_text(
        "cmd = [sys.executable, '-m', 'ingest.jobs.does_not_exist']\n"
        "run_script('gone.py')\n"
        "subprocess.run([py, 'scripts/ops/missing_script.py'])\n", encoding="utf-8")
    hits = manage.missing_job_targets(tmp_path, "ingest/jobs/x.py",
                                      (jobs / "x.py").read_text(encoding="utf-8").splitlines())
    assert any("ingest.jobs.does_not_exist" in h for h in hits)
    assert any("scripts/ops/missing_script.py" in h for h in hits)


def test_带存在性守卫的相对路径不许误报(tmp_path):
    """`Path("code/signal_engine.py")` 配 `.exists()` 是探测产物，不是要起的脚本。
    误报一次，这项就从此被忽略。"""
    jobs = tmp_path / "ingest" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "y.py").write_text(
        'for relative in (Path("config.json"), Path("code/signal_engine.py")):\n'
        "    path = run_dir / relative\n"
        "    if path.exists() and path.is_file():\n"
        "        pass\n", encoding="utf-8")
    assert manage.missing_job_targets(tmp_path, "ingest/jobs/y.py",
                                      (jobs / "y.py").read_text(encoding="utf-8").splitlines()) == []


def test_测试文件不参与这条判定(tmp_path):
    """测试里的字符串大量是 monkeypatch 目标名与临时文件名（`nope.py`、
    `duanxian.proc.kill_tree`），按"文件必须存在"判会让这项常驻红。"""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "t.py").write_text("monkeypatch.setattr('duanxian.proc.kill_tree', f)\n"
                                "p = tmp_path / 'nope.py'\n", encoding="utf-8")
    assert manage.missing_job_targets(tmp_path, "tests/t.py",
                                      (tests / "t.py").read_text(encoding="utf-8").splitlines()) == []


# ---------------------------------------------------------------- 启动补备份
# 这一组一律**造真实文件**（写空 dump 再把 mtime 拨旧），不 patch `backup_ages`：
# 钩子内部分支全靠 glob + stat + 锁文件的时间，patch 掉就等于只测了字符串。
def _dump(backup_root: Path, kind: str, name: str, age_hours: float, monkeypatch) -> Path:
    d = backup_root / kind
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_bytes(b"dump")
    ts = time.time() - age_hours * 3600
    os.utime(f, (ts, ts))
    monkeypatch.setenv("VIBE_BACKUP_DIR", str(backup_root))
    monkeypatch.setattr(manage, "_restorable", lambda p, ttl_hours=24.0: (True, ""))
    return f


def _no_spawn(monkeypatch, sink: list):
    """打 `_spawn_backup` 这个**唯一入口**，不打 `subprocess.Popen`。

    10-06 的教训：另一个会话把执行体从 `subprocess.run` 换成 `Popen` 之后，
    测试里假的 `run` 再也不被调用，pytest 就在用户桌面上真起了通达信。
    所以起子进程的东西必须在模块层留一个具名洞，测试钉那个洞。
    """
    monkeypatch.setattr(manage, "_spawn_backup", lambda argv, root, out: sink.append(argv))


def test_备份新就不补跑(tmp_path, monkeypatch):
    _dump(tmp_path / "bk", "daily", "daily_a.dump", 2, monkeypatch)
    _dump(tmp_path / "bk", "weekly", "weekly_a.dump", 24, monkeypatch)
    called = []
    _no_spawn(monkeypatch, called)
    assert "不旧" in manage.backup_on_start(tmp_path) and not called


def test_备份过期时后台补一次daily且参数是常量(tmp_path, monkeypatch):
    started = []
    _dump(tmp_path / "bk", "daily", "daily_a.dump", 201, monkeypatch)
    _dump(tmp_path / "bk", "weekly", "weekly_a.dump", 201, monkeypatch)
    _no_spawn(monkeypatch, started)
    monkeypatch.setattr(manage, "python_at", lambda root: Path("/x/python"))
    msg = manage.backup_on_start(tmp_path)
    assert "补跑" in msg and started
    assert started[0][-2:] == ["app.ops.backup", "daily"]      # 补的是 daily，不是 600MB 的全库
    assert "-m" in started[0] and not any(isinstance(a, str) and (";" in a or "&" in a)
                                          for a in started[0])  # 没有 shell、没有可拼接的输入
    # 触发理由必须只有真正过期且该由这条路补的那档：第一版这里写的是
    # 「因为 weekly 过期所以去补 daily」——动作对、理由是假的，那种字符串最容易被当成已修。
    assert "daily＝201 小时前" in msg and "weekly＝201 小时前" not in msg.split("日志")[0]
    assert "app.ops.backup weekly" in msg                     # weekly 另外指到人跑的命令


def test_正在写时不叠第二份(tmp_path, monkeypatch):
    """10-06 实测：17:41 我的钩子起的那份和 17:42 手工那份同时在长，
    两份 pg_dump 抢同一块盘，谁都不会更快。判据是「最新 dump 刚刚被改过」。"""
    started = []
    _dump(tmp_path / "bk", "daily", "daily_a.dump", 0.05, monkeypatch)   # 3 分钟前
    _no_spawn(monkeypatch, started)
    msg = manage.backup_on_start(tmp_path)
    assert not started and "不叠第二份" in msg


def test_weekly过期不许谎称已经补了全库(tmp_path, monkeypatch):
    started = []
    _dump(tmp_path / "bk", "daily", "daily_a.dump", 2, monkeypatch)
    _dump(tmp_path / "bk", "weekly", "weekly_a.dump", 240, monkeypatch)
    _no_spawn(monkeypatch, started)
    msg = manage.backup_on_start(tmp_path)
    assert not started and "weekly" in msg and "app.ops.backup weekly" in msg
    assert "已在后台补跑" not in msg


def test_补跑锁新鲜时不重复起(tmp_path, monkeypatch):
    started = []
    _dump(tmp_path / "bk", "daily", "daily_a.dump", 201, monkeypatch)
    _no_spawn(monkeypatch, started)
    lock = tmp_path / ".local" / "backup_on_start.daily.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("1", encoding="utf-8")
    msg = manage.backup_on_start(tmp_path)
    assert not started and "不重复起" in msg


def test_过期锁必须能重新拿起来(tmp_path, monkeypatch):
    """`O_EXCL` 不清旧锁 = 一次崩溃把这条通道永久焊死，而它看起来还在工作。
    第一版就是这个错，实测方式是造一份 1 小时前的锁。"""
    started = []
    _dump(tmp_path / "bk", "daily", "daily_a.dump", 201, monkeypatch)
    _no_spawn(monkeypatch, started)
    monkeypatch.setattr(manage, "python_at", lambda root: Path("/x/python"))
    lock = tmp_path / ".local" / "backup_on_start.daily.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("1", encoding="utf-8")
    ts = time.time() - 3600
    os.utime(lock, (ts, ts))
    assert "补跑" in manage.backup_on_start(tmp_path) and len(started) == 1


def test_读不到备份目录判红而不是说新(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_BACKUP_DIR", str(tmp_path / "no-backups-here"))
    monkeypatch.setenv("FT_BACKUP_DIR", str(tmp_path / "no-backups-here"))
    msg = manage.backup_on_start(tmp_path)
    assert "判不了" in msg and "备份新" in msg
    ok, action = manage.backup_freshness()
    assert ok is False and "不存在" in action


def test_可以用环境变量整个关掉(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_BACKUP_ON_START", "0")
    started = []
    _no_spawn(monkeypatch, started)
    assert "已关掉" in manage.backup_on_start(tmp_path) and not started


def test_起不来不许把网站带崩(tmp_path, monkeypatch):
    """钩子挂在启动路径最后：备份抛出去就等于"打开网站打不开"，那比不补备份更糟。"""
    _dump(tmp_path / "bk", "daily", "daily_a.dump", 201, monkeypatch)
    monkeypatch.setattr(manage, "python_at", lambda root: Path("/x/python"))

    def boom(argv, root, out):
        raise RuntimeError("起不来")
    monkeypatch.setattr(manage, "_spawn_backup", boom)
    msg = manage.backup_on_start(tmp_path)
    assert "起不来" in msg


def test_pytest进程里硬挡真实备份(tmp_path, monkeypatch):
    """这条不 patch 任何东西：证明 `_spawn_backup` 在 pytest 里**自己**就会挡，
    而不是靠某个测试恰好替它挡了。判据来自 10-06 那次真起了通达信的事故。"""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/x.py::t")
    with pytest.raises(RuntimeError, match="不许真起备份"):
        manage._spawn_backup(["python", "-m", "app.ops.backup", "daily"], tmp_path, None)


def _layout(root: Path):
    """体检项要在"什么都不像真项目"的目录里也能跑完，所以自己搭最小骨架。"""
    for name in ("requirements.txt", "runtime/package.json", "runtime/package-lock.json",
                 "frontend/package-lock.json", "frontend/src/main.ts", "frontend/dist/index.html"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")


def test_新项必须排在指纹项之前(tmp_path, monkeypatch):
    """`setup()` 用 `checks[:-1]` 剔掉**最后一项**（那时指纹还没落盘）。
    排在指纹之后的新项会被它顺手剔掉 —— 所以"顺序"本身就是判据，不是排版偏好。"""
    _layout(tmp_path)
    _write_loops(tmp_path, ("daily-close", "newswire"))
    monkeypatch.setattr(manage, "probe", lambda *a, **k: True)
    monkeypatch.setattr(manage, "dependency_status", lambda _: (True, ""))
    monkeypatch.setattr(manage.shutil, "which", lambda _: "/npm")
    monkeypatch.setattr(manage, "live_loops", lambda port, timeout=8.0: (
        {"host_loops": ["daily-close", "newswire"], "host_loops_missing": [], "pid": 1}, None))
    monkeypatch.setattr(manage, "data_freshness", lambda root: (True, "到位", {"expected": "2026-10-08"}))
    (tmp_path / ".local").mkdir(exist_ok=True)
    (tmp_path / ".local/setup.json").write_text(json.dumps({"fingerprint": "x"}))
    checks = manage.doctor(tmp_path)
    names = [c["name"] for c in checks]
    assert "常驻循环完整性" in names and names[-1] == "安装与源码一致"
    assert names.index("常驻循环完整性") < names.index("安装与源码一致")
    assert next(c for c in checks if c["name"] == "常驻循环完整性")["ok"] is True


def test_启动相位不许把循环项固定弄红(monkeypatch, tmp_path):
    """`start()` 先体检再起进程，所以"连不上服务"在启动那一刻是**流程决定的**，不是故障。
    不加这个区分，这项会在每次启动、在所有人最需要看清它的时候固定报一条红 ——
    而一条必然误报的体检项等于没有体检项。"""
    _write_loops(tmp_path, ("daily-close", "newswire"))
    monkeypatch.setattr(manage, "live_loops", lambda port, timeout=8.0: (None, "ConnectionRefused"))
    ok_startup, act_startup = manage.loop_inventory(tmp_path, 8910, "startup")
    ok_steady, act_steady = manage.loop_inventory(tmp_path, 8910, "steady")
    assert ok_startup and "启动" in act_startup.replace("绑定端口之前", "启动")
    assert not ok_steady and "服务没在跑" in act_steady


# ---------------------------------------------------------------- CLI 那三档读数
def test_CLI_status_分得清从未成功与无记录(home, capsys):
    """`scripts/jobs.py status` 原来把"没有记录"也印成「从未成功」——
    长假第一天整页"从未成功"，看着像全线故障。两种必须分开。"""
    from scripts import jobs as jobs_cli
    _state(home, {"watchlist": {"last_run_at": "2026-10-05 20:40:00", "last_ok_at": None,
                                "last_status": "fail", "consecutive_failures": 91,
                                "last_error": "not-null constraint"}})
    assert jobs_cli.cmd_status(None) == 0
    out = capsys.readouterr().out
    assert "从未成功（跑过 2026-10-05 20:40:00，连败 91）" in out
    assert "无运行记录（在册但至今没记过一笔" in out
    assert "daily_close" in out and "coverage" in out and "tdx_watch" in out


def test_在册清单比CLI多不许有人改不动(home):
    """CLI 在册的键必须覆盖 `CLOSE_DEFAULTS` 全部：少一个就是"页面上有开关、
    命令行改不动"，而这两个入口写的是同一份 config.json。"""
    from scripts import jobs as jobs_cli
    want, why = manage.declared_jobs()
    assert not why
    assert set(want) - set(jobs_cli._known()) == set(), "有作业在 CLOSE_DEFAULTS 里却不在 CLI 在册"


def test_循环都在但有条从不记账必须点名(monkeypatch, tmp_path):
    """10-06 的 `intraday`：线程活着、`state.json` 里连键都没有。
    "该起几条"全齐不等于"每条都在记账"，所以这一句必须挂在同一个体检项里。"""
    _write_loop_pairs(tmp_path, (("intraday", "intraday"), ("daily-close", "daily_close"),
                                 ("newswire", "news")))
    monkeypatch.setattr(manage, "live_loops",
                        lambda port, timeout=8.0: ({"host_loops": ["intraday", "daily-close", "newswire"],
                                                    "host_loops_missing": [], "pid": 1}, None))
    monkeypatch.setattr(manage, "state_keys", lambda: {"daily_close"})
    ok, action = manage.loop_inventory(tmp_path, 8910)
    assert ok and "零笔记账" in action and "intraday" in action and "newswire" in action
    # 已经记了账的那条不许被顺手点出来（`daily_close` 有记录 ⇒ 不该出现在"零笔"那一串里）
    assert "daily_close" not in action.split("零笔记账：")[1].split("——")[0]
    # ⚠️ 记账键不许由线程名猜出来（`newswire` 记到 `news`）：猜出来的键会把在记账的那条说成没记。
    assert [k for _n, k in manage.declared_loop_jobs(tmp_path)[0]] == ["intraday", "daily_close", "news"]


def test_记账键配对读不出来时不许硬编一份(monkeypatch, tmp_path):
    """兜底形状（只有名字清单）要把"配对读不到"说出来 —— 静默用 `name.replace('-','_')`
    猜键名，就会把 `newswire`→`news` 这类真实差异判成"从不记账"。"""
    _write_loops(tmp_path, ("newswire", "daily-close"))
    pairs, why = manage.declared_loop_jobs(tmp_path)
    assert why and "只读到名字清单" in why
    assert pairs == [("newswire", "newswire"), ("daily-close", "daily_close")]


# ---------------------------------------------------------------- 开关"当场生效不生效"
def _loop_body(name: str, path: str) -> str:
    """取某个循环函数/线程体的源码（到下一个顶层 `def ` 为止）。"""
    import re
    src = Path(path).read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^def {name}\(.*?(?=^def )", src, re.S | re.M)
    assert m, f"{path} 里找不到 {name}"
    return m.group(0)


def _thread_target(loop_name: str) -> tuple[str | None, str | None]:
    """从 `threading.Thread(target=_xxx, name="<loop_name>")` 那行**反查**线程体函数。

    为什么不在测试里手抄一张"循环名 → 函数名"的表：那样每加一条循环就得改测试，
    忘了改就是 `KeyError`（10-15 第一版正是这样红的）。反查的代价是测试跟着源码走 ——
    清单与实现不同步时它自己会发现，而不是靠人记得改注释。
    """
    for py in (manage.ROOT / "server.py", manage.ROOT / "vr" / "watchtower.py"):
        try:
            src = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"threading\.Thread\(\s*target=(\w+)[^)]*?name=[\"']"
                      + re.escape(loop_name) + "[\"']", src, re.S)
        if m:
            return m.group(1), str(py)
    return None, None


def _reads_master_switch(body: str) -> bool:
    """线程体里有没有"每轮重读总开关"的痕迹。两种写法都算（10-15 实测两种都在用）：

    · `_*_enabled(` / `enabled(` —— 独立的一个判据函数（intraday / newswire / ths_sync / backup…）；
    · `settings(...)` 配 `"enabled"` —— **跨行**的多判据（`daily-close` 就是这么读的：
      `jstore.settings(k, CLOSE_DEFAULTS[k])[0]` 上一行、`.get("enabled", True)` 下一行），
      所以这里看的是"这一体里既读了配置又读了 enabled 键"，不是单行正则。

    ⚠️ 只认 `settings(` 会误判：`tdx-watch` 每轮读的是 `client_mode` 那个**子开关**，
    总开关仍只在 `_start_tdx_watch()` 里读一次 ⇒ 它属于"要重启"那一档。
    """
    if re.search(r"(?:\w+_)?enabled\(", body):
        return True
    return bool(re.search(r"settings\(", body) and re.search(r"""["']enabled["']""", body))


def test_声明即时生效的那几条循环体里必须真读开关():
    """面板上"下一个心跳就生效"这句话**不许套统一口径**：只有循环体里真读总开关的那几条成立。

    这条钉的是**声明与源码一致**：补完每轮重读就把键名挪进 `LIVE_SWITCH_LOOPS`，不用改措辞；
    反过来谁偷偷新增一条"即时生效"的说法，这里当场红。
    线程体靠 `_thread_target()` 从 `threading.Thread(...)` 那行**反查**，不手抄对照表 ——
    手抄的表每加一条循环就得改（10-15 就是这么红过一次 `KeyError`）。
    """
    for loop in jobsboard.LIVE_SWITCH_LOOPS:
        pair = next((p for p in jobsboard.LOOP_JOBS if p[0] == loop), None)
        assert pair, f"{loop} 在 LIVE 里却不在 LOOP_JOBS 清单里"
        assert pair[1] in jobsboard.LOOP_OFF_EFFECT
        fname, path = _thread_target(loop)
        assert fname and path, f"{loop}：找不到起这条线程的地方，声明无从核对"
        assert _reads_master_switch(_loop_body(fname, path)), \
            f"{loop} 说即时生效，但线程体 {fname}() 里没有总开关的每轮读法"


def test_没补每轮重读的那几条必须明写要重启():
    dead = set(jobsboard.DEAD_UNTIL_RESTART_LOOPS)
    assert dead == {"watchlist-sync", "fundflow-sina", "coverage-precompute", "tdx-watch"}
    for loop in dead:
        fname, path = _thread_target(loop)
        assert fname and path, f"{loop}：找不到线程体，判不了"
        assert not _reads_master_switch(_loop_body(fname, path)), \
            f"{loop} 其实已经在每轮重读总开关了，该把它移到 LIVE_SWITCH_LOOPS"
    rows = {r["loop"]: r for r in jobsboard.loop_switches()["loops"]}
    for loop in dead:
        assert rows[loop]["live_switch"] is False
        assert "重启 8910" in rows[loop]["switch_effect"]
    for loop in jobsboard.LIVE_SWITCH_LOOPS:
        assert rows[loop]["live_switch"] is True


def test_关一条要重启才停的循环时回答里要带上这句(tmp_path, monkeypatch):
    """写入成功了却不说"还要重启"，用户就会以为已经停了 —— 这是最难查的一类假生效。"""
    home = tmp_path / "h5"
    (home / "jobs").mkdir(parents=True)
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    status, resp = jobsboard.set_switch("tdx_watch", False, via="test")
    assert status == 200 and "重启 8910" in resp["reason"]
    status, resp = jobsboard.set_switch("tdx_watch", True, via="test")
    assert status == 200 and "重启 8910" in resp["reason"]
    status, resp = jobsboard.set_switch("daily_close", False, via="test")
    assert status == 200 and "下一个心跳" in resp["reason"]


# ---------------------------------------------------------------- 频度：每日 / 每周
def test_只有默认值里有_run_freq_的键才交这两个字段():
    rows = {r["key"]: r for r in jobsboard.loop_switches()["loops"]}
    assert rows["ths_sync"]["has_freq"] is True and rows["ths_sync"]["run_freq"]
    for key in ("coverage", "fundflow", "watchlist", "tdx_watch"):
        assert rows[key]["has_freq"] is False and rows[key]["run_freq"] is None
        assert rows[key]["weekly_days"] is None


def test_选了每周却没勾周几必须当场说出来(tmp_path, monkeypatch):
    """`freq_allows` 是"判不了就放行"（配置写坏了不该让循环静默停产），于是
    `weekly` + 空 `weekly_days` = **天天跑**，与用户选的完全相反。宿主侧保留照跑，
    面板侧必须把这句话交出去，否则就是个没人发现的假设置。"""
    home = tmp_path / "h6"
    (home / "jobs").mkdir(parents=True)
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    status, _resp = jobsboard.set_knob("ths_sync", "run_freq", "weekly", via="test")
    assert status == 200
    row = next(r for r in jobsboard.loop_switches()["loops"] if r["key"] == "ths_sync")
    assert row["run_freq"] == "weekly" and row["freq_warning"] and "天天跑" in row["freq_warning"]
    status, _resp = jobsboard.set_knob("ths_sync", "weekly_days", "0,2,4", via="test")
    assert status == 200
    row = next(r for r in jobsboard.loop_switches()["loops"] if r["key"] == "ths_sync")
    assert row["weekly_days"] == "0,2,4" and row["freq_warning"] is None


def test_坏值仍由_jobs_那一层拒掉而不是面板各写一份(tmp_path, monkeypatch):
    home = tmp_path / "h7"
    (home / "jobs").mkdir(parents=True)
    monkeypatch.setenv("ASTOCK_DATA_HOME", str(home))
    status, resp = jobsboard.set_knob("ths_sync", "run_freq", "monthly", via="test")
    assert status == 400 and "daily / weekly" in resp["reason"]
    status, resp = jobsboard.set_knob("ths_sync", "weekly_days", "1,8", via="test")
    assert status == 400 and "0~6" in resp["reason"]


# ---------------------------------------------------------------- 计划任务那一格（10-08）
def _fake_ps(monkeypatch, stdout: str, rc: int = 0):
    blob = stdout.encode("utf-8")

    class R:
        returncode = rc
        stdout = blob
    monkeypatch.setattr(manage.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(manage, "PLATFORM", "nt")
    return R


def test_本项目注册的任务要现读现说而不是文档里那句零条(monkeypatch):
    """10-08 起本机有一条 `YuanShan-KeepAlive8910`（只拉服务、不跑数据）。
    文档与体检都必须跟着改口 —— "一条都没注册"这句已经是历史。"""
    out = ('"Name","State","Result"\r\n'
           '"YuanShan-KeepAlive8910","Ready","0"\r\n')
    _fake_ps(monkeypatch, out)
    rows, err = manage.own_scheduled_tasks()
    assert err is None and rows == [("YuanShan-KeepAlive8910", "Ready", "0")]


def test_CRLF的表头不许被当成一条任务(monkeypatch):
    """`splitlines()[1:]` 在 CRLF 输出上**去不掉表头**（第一行会变成 `"Name"`），
    页面上就多一行假任务、`State` 空着 —— 这种"多出来的一行"最难被看出来。"""
    out = '"Name","State","Result"\r\n"YuanShan-KeepAlive8910","Ready","0"\r\n'
    _fake_ps(monkeypatch, out)
    rows, _err = manage.own_scheduled_tasks()
    assert [r[0] for r in rows] == ["YuanShan-KeepAlive8910"]
    assert all(r[1] for r in rows)


def test_powershell那条命令的运算符不许拼成双横线(monkeypatch):
    """第一版把模板写成 `-{name_filter}` 而传进去的值自带横线 ⇒ `--like`，
    powershell 解析失败 ⇒ 这一格永远报"读不到"。所以命令串本身要钉住。"""
    seen = []

    def capture(argv, **kw):
        seen.append(argv[-1])

        class R:
            returncode = 0
            stdout = b'"Name","State","Result"\r\n"A","Ready","0"\r\n'
        return R()
    monkeypatch.setattr(manage.subprocess, "run", capture)
    monkeypatch.setattr(manage, "PLATFORM", "nt")
    manage.own_scheduled_tasks()
    manage.foreign_scheduled_tasks()
    assert "TaskName -like" in seen[0] and "--like" not in seen[0]
    assert "TaskName -notlike" in seen[1] and "--notlike" not in seen[1]


def test_查不到任务时要说不信而不是当好(monkeypatch):
    _fake_ps(monkeypatch, "", rc=1)
    rows, err = manage.own_scheduled_tasks()
    assert rows == [] and err and "不可信" in err


# ---------------------------------------------------------------- 体检项不许抄第二遍判据

def test_doctor的待人工读数就是gui_trail那份(monkeypatch, tmp_path):
    """10-13 现场：页面（`gui_trail.pending()`）说 **0 条要人补**，而 `manage.py doctor` 说
    **3 条没做成**。原因不是数据坏，是 doctor 里另写了一遍"数非 done 的条目"——
    那份抄本不认同日同步骤去重、也不认 `supersedes`（程序后来自己做完了）。
    同一件事两个读数比没有读数更坏：人会去补一件已经做完的事。
    """
    from duanxian import gui_trail
    monkeypatch.setattr(gui_trail, "data_path", lambda *p: str(tmp_path / "trail"))

    gui_trail.add(step='close_client', what='带外该关通达信但没关掉', status='need_human',
                  detail='停在确认框上', target='2026-10-13')
    ok, msg = manage.tdx_gui_trail()
    assert ok is False and "1 条" in msg, msg

    # 程序后来自己把这件事做完了（带 supersedes 的 done）⇒ 体检与页面要同时改口，不是只有一边
    gui_trail.add(step='close_client', what='带外已关闭通达信客户端', status='done',
                  target='2026-10-13', supersedes=True)
    ok2, msg2 = manage.tdx_gui_trail()
    assert ok2 is True and "没有要人补的" in msg2, msg2
    assert ok2 == (gui_trail.pending() == []), "体检的口径必须就是那份判据的返回值，不是自己再算一遍"

    src = (Path(__file__).resolve().parents[1] / "scripts" / "manage.py").read_text(encoding="utf-8")
    body = src.split("def tdx_gui_trail(")[1].split("\ndef ")[0]
    assert 'status != "done"' not in body, "这一项里不许再有第二份数法"
    assert "gui_trail.pending(" in body


def test_加一条循环两处清单必须一起长():
    """`LOOP_JOBS`（面板与体检）与 `CLOSE_DEFAULTS`（开关与记账的默认值）少对一处，
    就长出"看得见关不掉"或"改得动没人读"。10-15 接第 12 条（消息推送）时立的这条。

    唯一合法的不对称是 `fill_all`：它**不是一条自己的线程**，而是 `daily-close` 那一条里的
    第二段（15:59 获取段跑完才起加工段），所以它出现在 `CLOSE_DEFAULTS`（有开关、会记账）
    却不出现在 `LOOP_JOBS`（没有对应的线程名）。新增键要按这条判：
    **要么加一行 `LOOP_JOBS`，要么像 `fill_all` 一样是某条线程内的一个段** —— 两处都不是就是漏。
    """
    from duanxian import jobs as jstore
    keys_in_board = {p[1] for p in jobsboard.LOOP_JOBS}
    assert set(jstore.CLOSE_DEFAULTS) - keys_in_board == {"fill_all"}, \
        "CLOSE_DEFAULTS 里出现了既不是一条线程、也不是段的新键：面板看不见也就关不掉"
    assert keys_in_board - set(jstore.CLOSE_DEFAULTS) == {"news"}, \
        "LOOP_JOBS 里出现了没有默认值登记的键（news 走 newswire 自己那份，是唯一例外）"
    assert "notify_push" in keys_in_board and "notify-push" in jobsboard.HOST_LOOPS
    rows = {r["key"]: r for r in jobsboard.loop_switches()["loops"]}
    assert rows["notify_push"]["loop"] == "notify-push"
    assert rows["notify_push"]["alive"] in (True, False)     # 现读，不假设它起没起
    assert rows["notify_push"]["has_slot"] is False     # 几点发是每条计划自己的字段，不给假框
    assert "永久少一天" not in (rows["notify_push"]["off_effect"] or "")   # 关掉只丢推送，不丢数据
