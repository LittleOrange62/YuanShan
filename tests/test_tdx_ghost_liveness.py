# -*- coding: utf-8 -*-
"""判活口径：**残影条目不算「客户端在跑」**，收进程按 pid 精确收（2026-11-11）。

那一天的现场（实测数字，不是设想）：
`TdxW.exe`(45956) 起于 11-10 15:39:11，线程 0、工作集 20 K、顶层窗口 0 个、映像路径读不出来，
而 `tasklist` 照样列它一行。旧判据只看镜像名 ⇒ 认定"客户端还在跑" ⇒ 每轮都先关它 ⇒
`taskkill /F /IM` 没有线程可杀、关不掉 ⇒ 等 20s 判「旧进程关不掉」转人工；
而那条按名字杀的命令还把 23:50:09 刚拉起的新实例 17880 一起收掉了。
15:38 定时重启、16:00/16:24/17:30 三趟收盘链、23:49 手动那趟，全卡在同一步；
vipdoc 到目标日的文件数 0（前一场 9,531），库里 11-11 的日线/指数各 0 行。

所以这里钉三件事：
① 判活只认线程数，残影单独交出来（看得见，但不算在跑、也不等它消失）；
② 收进程按 pid（带子树），**绝不按镜像名群杀**；
③ 只有"活着但不可用"才值得先切 —— 可用就复用（用户原话：「不要每次无脑都强制重启」）。
"""
from __future__ import annotations

import os

import pytest

from app.tdx_client import locator, reset, watchman, win32
from app.tdx_client.steps import s1_launch, s8_finish


class _Ctx:
    """`RunContext` 的最小替身：s1 只用到 log 与 bag。"""

    def __init__(self, main=None):
        self.bag: dict = {}
        self.lines: list[str] = []
        self._main = main

    def log(self, msg):
        self.lines.append(str(msg))

    def refresh_main(self, **kw):
        return self._main


def _win(pid, text='通达信金融终端V7.73'):
    return type('W', (), {'pid': pid, 'hwnd': 1000 + pid, 'text': text})()


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(s1_launch.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(reset.time, 'sleep', lambda _s: None)


# ---------------------------------------------------------------- ① 判活

def test_残影条目不算在跑(monkeypatch):
    monkeypatch.setattr(win32, 'pids_by_name', lambda n: [45956, 17880])
    monkeypatch.setattr(win32, 'thread_counts', lambda: {17880: 12, 999: 3})
    assert win32.split_live('TdxW.exe') == ([17880], [45956])
    assert win32.live_pids('TdxW.exe') == [17880]
    assert win32.ghost_pids('TdxW.exe') == [45956]
    assert win32.process_alive('TdxW.exe') is True


def test_只剩残影时判没在跑(monkeypatch):
    """这一条就是 11-11 那一整天的根因：旧写法在这里回 True。"""
    monkeypatch.setattr(win32, 'pids_by_name', lambda n: [45956])
    monkeypatch.setattr(win32, 'thread_counts', lambda: {999: 3})
    assert win32.live_pids('TdxW.exe') == []
    assert win32.ghost_pids('TdxW.exe') == [45956]
    assert win32.process_alive('TdxW.exe') is False


def test_线程快照拍不到时保守当活着(monkeypatch):
    """探不到 ≠ 没有：快照失败就把客户端判成"没在跑"，会去起第二台抢同一个 vipdoc。"""
    monkeypatch.setattr(win32, 'pids_by_name', lambda n: [111, 222])
    monkeypatch.setattr(win32, 'thread_counts', lambda: {})
    assert win32.split_live('TdxW.exe') == ([111, 222], [])


def test_真机拍一次线程快照_不打桩():
    """桩的护栏：真 API 的签名/结构体布局错了，打桩的用例一条都测不出来。
    只读进程表，不碰桌面、不起进程。"""
    counts = win32.thread_counts()
    assert isinstance(counts, dict) and counts, '本机至少该拍到几百个线程'
    assert counts.get(os.getpid(), 0) > 0, '自己这个进程的线程数必须拍得到'


def test_真机判活与进程表自洽_不打桩():
    for name in reset.EXACT_NAMES:
        listed = win32.pids_by_name(name)
        live, ghost = win32.split_live(name)
        assert sorted(live + ghost) == sorted(listed), (name, listed, live, ghost)
        assert not (set(live) & set(ghost))


# ---------------------------------------------------------------- ② 收进程

def _rec_run(monkeypatch):
    calls: list[list[str]] = []

    def fake(argv, **kw):
        calls.append(list(argv))
        return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

    monkeypatch.setattr(win32.subprocess, 'run', fake)
    return calls


def test_收进程只按pid_绝不按镜像名群杀(monkeypatch):
    monkeypatch.setattr(win32, 'pids_by_name', lambda n: [45956, 17880])
    monkeypatch.setattr(win32, 'thread_counts', lambda: {17880: 9})
    calls = _rec_run(monkeypatch)
    assert win32.kill('TdxW.exe') is True
    assert calls == [['taskkill', '/F', '/T', '/PID', '17880']], calls
    assert not any('/IM' in c for c in calls), '按镜像名群杀会连刚拉起的新实例一起收掉'


def test_只有残影时一条taskkill都不发(monkeypatch):
    monkeypatch.setattr(win32, 'pids_by_name', lambda n: [45956])
    monkeypatch.setattr(win32, 'thread_counts', lambda: {999: 1})
    calls = _rec_run(monkeypatch)
    assert win32.kill('TdxW.exe') is False
    assert calls == [], '残影没有线程可杀，发命令只是白等'


def test_清场时残影不参与等它消失(monkeypatch, no_sleep):
    monkeypatch.setattr(win32, 'pids_by_name',
                        lambda n: [45956] if n == 'TdxW.exe' else [])
    monkeypatch.setattr(win32, 'pids_by_prefix', lambda pre: [])
    monkeypatch.setattr(win32, 'thread_counts', lambda: {999: 1})
    assert reset.snapshot() == []
    assert reset.ghosts() == [('TdxW.exe', 45956)]
    ok, why = reset.kill_all()
    assert ok is True, why
    assert '残影' in why and '等了' not in why, why


def test_清场按pid收活着的实例(monkeypatch, no_sleep):
    state = {'pids': [111, 222]}
    monkeypatch.setattr(win32, 'pids_by_name',
                        lambda n: list(state['pids']) if n == 'TdxW.exe' else [])
    monkeypatch.setattr(win32, 'pids_by_prefix', lambda pre: [])
    monkeypatch.setattr(win32, 'thread_counts', lambda: {111: 5, 222: 6, 999: 1})
    killed: list[list[int]] = []

    def fake_kill(pids):
        killed.append(list(pids))
        state['pids'] = []
        return {p: True for p in pids}

    monkeypatch.setattr(win32, 'kill_pids', fake_kill)

    def boom(name):
        raise AssertionError('清场不许再走按镜像名杀那条路')

    monkeypatch.setattr(win32, 'kill', boom)
    ok, why = reset.kill_all()
    assert ok is True, why
    assert killed == [[111, 222]], killed


# ---------------------------------------------------------------- ③ s1 只在不可用时切

def test_残影不挡启动_也不去杀它(monkeypatch, tmp_path, no_sleep):
    exe = tmp_path / 'TdxW.exe'
    exe.write_bytes(b'')
    monkeypatch.setattr(s1_launch, 'exe_path', lambda: exe)
    monkeypatch.setattr(win32, 'split_live', lambda n: ([], [45956]))
    monkeypatch.setattr(win32, 'live_pids', lambda n: [])
    kills: list = []
    monkeypatch.setattr(win32, 'kill_pids', lambda pids: kills.append(list(pids)) or {})
    pops: list = []
    monkeypatch.setattr(s1_launch.subprocess, 'Popen',
                        lambda argv, **kw: pops.append(list(argv)))
    ctx = _Ctx()
    res = s1_launch.LaunchStep().act(ctx)
    assert res.ok, res.message
    assert len(pops) == 1, '残影在场就不敢起新实例 = 11-11 那一天的形状'
    assert kills == [], '残影杀不掉，一次都不该发'
    assert any('残影' in ln for ln in ctx.lines), ctx.lines


def test_活着但不可用才按pid收(monkeypatch, tmp_path, no_sleep):
    exe = tmp_path / 'TdxW.exe'
    exe.write_bytes(b'')
    monkeypatch.setattr(s1_launch, 'exe_path', lambda: exe)
    monkeypatch.setattr(win32, 'split_live', lambda n: ([111], [45956]))
    monkeypatch.setattr(win32, 'live_pids', lambda n: [])
    kills: list = []
    monkeypatch.setattr(win32, 'kill_pids',
                        lambda pids: kills.append(list(pids)) or {p: True for p in pids})
    pops: list = []
    monkeypatch.setattr(s1_launch.subprocess, 'Popen',
                        lambda argv, **kw: pops.append(list(argv)))
    res = s1_launch.LaunchStep().act(_Ctx())
    assert res.ok, res.message
    assert kills == [[111]], kills
    assert len(pops) == 1


def test_活实例真收不掉才算失败(monkeypatch, tmp_path, no_sleep):
    exe = tmp_path / 'TdxW.exe'
    exe.write_bytes(b'')
    monkeypatch.setattr(s1_launch, 'exe_path', lambda: exe)
    monkeypatch.setattr(win32, 'split_live', lambda n: ([111], []))
    monkeypatch.setattr(win32, 'kill_pids', lambda pids: {p: False for p in pids})
    monkeypatch.setattr(s1_launch.LaunchStep, 'wait_until',
                        lambda self, fn, **kw: (False, '等待 20s 超时；最后一次检查：进程仍在'))
    pops: list = []
    monkeypatch.setattr(s1_launch.subprocess, 'Popen',
                        lambda argv, **kw: pops.append(list(argv)))
    res = s1_launch.LaunchStep().act(_Ctx())
    assert not res.ok and '旧进程关不掉' in res.message, res.message
    assert pops == [], '旧的没退就起新的 = 通达信检测到已有实例后静默退出'


def test_verify的pid取真有窗口的那个(monkeypatch):
    monkeypatch.setattr(win32, 'split_live', lambda n: ([17880], [45956]))
    monkeypatch.setattr(s1_launch, '_login_box', lambda pid=None: None)
    monkeypatch.setattr(s1_launch.LaunchStep, 'wait_until', lambda self, fn, **kw: fn())
    ctx = _Ctx(main=_win(17880))
    ok, why = s1_launch.LaunchStep().verify(ctx)
    assert ok and ctx.bag['pid'] == 17880, (ok, why, ctx.bag)
    assert '17880' in why


def test_只有残影时verify判进程不在(monkeypatch):
    monkeypatch.setattr(win32, 'split_live', lambda n: ([], [45956]))
    monkeypatch.setattr(s1_launch.LaunchStep, 'wait_until', lambda self, fn, **kw: fn())
    ok, why = s1_launch.LaunchStep().verify(_Ctx())
    assert not ok and '残影' in why, why


# ---------------------------------------------------------------- ④ 探针的六档

@pytest.fixture
def desktop(monkeypatch):
    """把探针要枚举的那几样都摆好，默认一台健康客户端。"""
    monkeypatch.setattr(locator, 'sync_running', lambda: (False, '没有 TPyth* 进程'))
    monkeypatch.setattr(s8_finish, '_minimized', lambda w: False)
    monkeypatch.setattr(s1_launch, '_login_box', lambda pid=None: None)
    monkeypatch.setattr(win32, 'window_hung', lambda hwnd: False)
    monkeypatch.setattr(locator, 'main_window', lambda **kw: _win(111))
    monkeypatch.setattr(win32, 'split_live', lambda n: ([111], []))
    return monkeypatch


def test_健康客户端判可用(desktop):
    st = watchman.probe()
    assert st['state'] == 'ok' and st['usable'] is True and st['running'] is True
    assert st['live'] == [111] and st['ghost'] == []


def test_只剩残影_探针说没在跑且不可用(desktop):
    desktop.setattr(win32, 'split_live', lambda n: ([], [45956]))
    desktop.setattr(locator, 'main_window', lambda **kw: None)
    st = watchman.probe()
    assert st['running'] is False and st['usable'] is False and st['state'] == 'ghost'
    assert st['ghost'] == [45956] and '残影' in st['reason'], st


def test_停在登录框_不可用(desktop):
    desktop.setattr(s1_launch, '_login_box', lambda pid=None: _win(111))
    st = watchman.probe()
    assert st['state'] == 'login' and st['usable'] is False and st['login_box'] is True


def test_缩到托盘_仍算可用(desktop):
    """主窗口枚举不到 ≠ 没登录。判成不可用就会去切一台好好的客户端。"""
    desktop.setattr(locator, 'main_window', lambda **kw: None)
    st = watchman.probe()
    assert st['state'] == 'tray' and st['usable'] is True, st


def test_假死_不可用(desktop):
    desktop.setattr(win32, 'window_hung', lambda hwnd: True)
    st = watchman.probe()
    assert st['state'] == 'hung' and st['usable'] is False and '假死' in st['reason']


def test_响应判不了_不算假死(desktop):
    desktop.setattr(win32, 'window_hung', lambda hwnd: None)
    st = watchman.probe()
    assert st['hung'] is None and st['usable'] is True and st['state'] == 'ok'


def test_六档措辞只住一份():
    """页面与台账都引 `STATE_LABELS`，不许在别处抄第二份中文。"""
    assert set(watchman.STATE_LABELS) == {'ok', 'tray', 'login', 'hung', 'ghost', 'off'}
    assert set(watchman.USABLE_STATES) == {'ok', 'tray'}
    for key, label in watchman.STATE_LABELS.items():
        assert label and '**' not in label, (key, label)