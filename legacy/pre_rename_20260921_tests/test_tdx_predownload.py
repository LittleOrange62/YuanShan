# -*- coding: utf-8 -*-
"""
通达信 P0 前置下载这条链路（`scripts/ops/p0_tdx_predownload.py` +
`app/tdx_client/reset.py` + `run_agent --attempts`）。

**刻意不动通达信界面**：能不能点开菜单取决于当时的桌面会话，测试挂在那上面
等于把断言变成天气预报。这里只钉住"改代码时最容易悄悄弄坏"的那几件事：

- 分步调用里写的步骤 key 必须真的存在（改名 s7_minute 就该在这里红）；
- minute 那一步必须自己带上 s5_dialog —— 每个 run_agent 子进程结束都会
  `s8_finish.cleanup()` 把对话框关掉，不重开的话 s7 一定失败；
- `reset.kill_all` 要**等到进程真的消失**，不能 taskkill 完就返回成功；
- 作业登记与 e1 追平链上有它，不然"固化"只是写了个脚本没人调。
"""
import importlib.util
from pathlib import Path

from app.tdx_client import reset, steps

ROOT = Path(__file__).resolve().parent.parent
P0 = ROOT / 'scripts' / 'ops' / 'p0_tdx_predownload.py'


def load_p0():
    """按文件路径 import —— scripts/ 不是包，没法 `import scripts.ops.x`。"""
    spec = importlib.util.spec_from_file_location('p0_tdx_predownload', P0)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                      # type: ignore[union-attr]
    return mod


# ------------------------------------------------------------ 分步调用
def test_phases_only_reference_real_steps():
    """
    每个 phase 里的 key 都要在 `steps.STEP_BY_KEY` 里。写错的后果是
    `run_agent` 直接 `KeyError: 没有这些步骤`，而那时人已经在等下载了。
    """
    p0 = load_p0()
    known = set(steps.STEP_BY_KEY)
    for name, _what, keys, _timeout in p0.PHASES:
        assert set(keys) <= known, f'{name} 里有不存在的步骤：{set(keys) - known}'


def test_phases_cover_login_day_and_minute_in_order():
    p0 = load_p0()
    names = [n for n, _, _, _ in p0.PHASES]
    assert names == ['prepare', 'day', 'minute']
    by_name = {n: keys for n, _, keys, _ in p0.PHASES}
    assert 's6_day' in by_name['day']
    assert 's7_minute' in by_name['minute']


def test_download_phases_reopen_the_dialog_themselves():
    """
    **这是最容易踩的一条。** run_agent 的 `finally` 无条件跑
    `s8_finish.cleanup()`，对话框会被关掉、通达信被最小化；所以下一个子进程
    必须自己先 s5_dialog，否则 s7 只会报"对话框不在了"。
    """
    p0 = load_p0()
    for name, _what, keys, _timeout in p0.PHASES:
        if name == 'prepare':
            continue
        assert keys[0] == 's5_dialog', f'{name} 没有先打开对话框'


def test_minute_phase_gets_the_longest_timeout():
    """分钟线实测 7 分 41 秒，是这条链最慢的一段 —— 超时不能按日线那 10 秒配。"""
    p0 = load_p0()
    t = {n: to for n, _, _, to in p0.PHASES}
    assert t['minute'] > t['day']
    assert t['minute'] >= 3600


def test_stall_limit_is_shorter_than_every_phase_timeout():
    """
    "多久没出声算卡死"必须**短于**每一步的总超时，否则它永远轮不到触发，
    真卡住时要等满一小时才发现。
    """
    p0 = load_p0()
    assert all(to > p0.STALL_LIMIT for _, _, _, to in p0.PHASES)
    assert p0.HEARTBEAT < p0.STALL_LIMIT


# ------------------------------------------------------------ 从头再来
def test_reset_targets_the_main_exe_and_the_sync_process():
    """
    只杀 `TdxW.exe` 是不够的：后台同步进程还在跑时会跟新起的通达信抢 vipdoc，
    表现是"下载完毕"了但文件时间戳没变。
    """
    assert 'TdxW.exe' in reset.EXACT_NAMES
    assert reset.PREFIXES == ('TPyth',)


def test_kill_all_waits_until_the_processes_are_really_gone(monkeypatch):
    """
    `taskkill /F` 返回成功 ≠ 进程已经没了。立刻重启的话通达信会检测到已有实例
    然后静默退出，表现是"启动了但一直没窗口"。
    """
    seen = []
    calls = {'n': 0}

    def fake_by_name(name):
        # 前两次还在，第三次开始没了
        return [123] if name == 'TdxW.exe' and calls['n'] < 2 else []

    def fake_by_prefix(_prefix):
        return []

    def fake_kill(name):
        seen.append(name)
        return True

    def fake_sleep(_s):
        calls['n'] += 1

    monkeypatch.setattr(reset.win32, 'pids_by_name', fake_by_name)
    monkeypatch.setattr(reset.win32, 'pids_by_prefix', fake_by_prefix)
    monkeypatch.setattr(reset.win32, 'kill', fake_kill)
    monkeypatch.setattr(reset.time, 'sleep', fake_sleep)

    ok, why = reset.kill_all()
    assert ok, why
    assert seen == ['TdxW.exe'], '只该杀真的在跑的那些'
    assert calls['n'] >= 2, '没等进程消失就返回了'


def test_kill_all_reports_failure_instead_of_raising(monkeypatch):
    """清不干净要**返回** False 让重试循环自己决定，不能抛出去打断整条链。"""
    monkeypatch.setattr(reset.win32, 'pids_by_name',
                        lambda n: [7] if n == 'TdxW.exe' else [])
    monkeypatch.setattr(reset.win32, 'pids_by_prefix', lambda p: [])
    monkeypatch.setattr(reset.win32, 'kill', lambda n: False)
    monkeypatch.setattr(reset.time, 'sleep', lambda s: None)
    monkeypatch.setattr(reset, 'GONE_TIMEOUT', 0.0)
    ok, why = reset.kill_all()
    assert not ok
    assert 'TdxW.exe(7)' in why


def test_kill_all_is_a_noop_when_nothing_runs(monkeypatch):
    monkeypatch.setattr(reset.win32, 'pids_by_name', lambda n: [])
    monkeypatch.setattr(reset.win32, 'pids_by_prefix', lambda p: [])
    ok, why = reset.kill_all()
    assert ok and '本来就没有' in why


def test_run_agent_takes_attempts_and_resets_between_rounds():
    """
    `--attempts` 的语义是"清进程 + 从 s1 整条重来"。这里卡两件事：参数在，
    而且重来那一段真的调了 `reset.kill_all`（只加参数不清进程等于原地重演失败）。
    """
    src = (ROOT / 'app' / 'tdx_client' / 'run_agent.py').read_text(
        encoding='utf-8')
    assert "'--attempts'" in src
    assert 'reset.kill_all' in src


# ------------------------------------------------------------ 接进链路
def test_the_predownload_is_registered_as_a_job_with_a_runner():
    """
    脚本写完不接进去等于没固化。这里同时卡 `RUNNERS`、seed 和库里那一行 ——
    只改库不改 seed，下次重建库这条作业就没了。
    """
    from app.db import engine
    from app.orchestrator import runner

    assert 'tdx_predownload' in runner.RUNNERS
    script = ROOT / runner.RUNNERS['tdx_predownload']['script']
    assert script.exists(), f'{script} 不存在'
    seed = (ROOT / 'app' / 'db' / 'sql' / '05_seed.sql').read_text(
        encoding='utf-8')
    assert "'tdx_predownload'" in seed
    job = engine.query_one(
        "SELECT * FROM config.job WHERE job_key = 'tdx_predownload'")
    assert job, 'tdx_predownload 没登记到 config.job'
    assert runner.job_runner(job) == 'tdx_predownload'
    # 目标日必须传下去：它要判"vipdoc 到没到这一天"
    assert runner.RUNNERS['tdx_predownload']['date'] == 'iso'


def test_fill_all_runs_the_predownload_first():
    """
    日线主源是 vipdoc，文件不刷新后面全是空跑 —— 所以它必须是追平链的第一步。
    """
    src = (ROOT / 'scripts' / 'ops' / 'e1_fill_all.py').read_text(
        encoding='utf-8')
    assert 'p0_tdx_predownload.py' in src
    assert src.index("('tdxdl'") < src.index("('daily'")


def test_catchup_delegates_to_the_predownload_script():
    """
    c10 里那个"vipdoc 落后就让通达信去下"的动作要走 p0 脚本，
    不能自己再直接调一遍 run_agent —— 那样重来机制和卡死判定要写两份。
    """
    src = (ROOT / 'scripts' / 'ingest' / 'c10_catchup.py').read_text(
        encoding='utf-8')
    assert 'p0_tdx_predownload.py' in src
    assert 'app.tdx_client.run_agent' not in src
