# -*- coding: utf-8 -*-
"""自动执行机制这轮加固的回归（2026-10-06）。

钉的是用户点名的那三件事里**我能 own 的两件**，外加一件自查时挖出来的：

1. **两条链起子作业都没有上限** —— `daily_close_sync._run` 用 `subprocess.call`，
   `e1_fill_all.run_script` 用 `Popen` + 读 stdout + `wait()`，一步卡死（GUI 等不到按钮、
   网络半连）就把整条链钉住；而宿主那层的段超时只掐**直接子进程**，被它再起的
   `c1/c3/c16/p0/run_agent` 变孤儿继续跑 —— 表现是"链断了但机器还在忙"。
2. **通达信进程"有监控"是假的** —— `app/tdx_client/watchman.py` 写好了 `probe()/heal()`
   却**全仓零引用**。现在只接只读探测，自愈仍要用户点头。
3. **超时的值必须按实测给** —— `d1_indicators --recent 5` 正常就要 1002 秒，
   统一拍一个 900 秒会把**正常跑完的那一步自己掐死**（掐完写 0 行还自称试过）。
"""
from __future__ import annotations

import subprocess

import pytest


# ---------------------------------------------------------------- 收进程树

def test_收进程树只按pid绝不按映像名(monkeypatch):
    """`taskkill /IM TdxW.exe` 那种写法会把用户桌面上正开着的客户端一起干掉
    （参考仓库那条 `FinanceTool-DailyCloseSync` 就是这么干的）。"""
    from duanxian import proc

    seen = {}

    def fake_run(cmd, **kw):
        seen['cmd'] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, '成功', '')

    monkeypatch.setattr(proc.subprocess, 'run', fake_run)
    monkeypatch.setattr(proc.os, 'name', 'nt')
    note = proc.kill_tree(4321)
    assert seen['cmd'][:2] == ['taskkill', '/PID'] and '4321' in seen['cmd']
    assert '/T' in seen['cmd'] and '/F' in seen['cmd']
    assert '/IM' not in seen['cmd'], '按映像名杀会连用户的通达信一起杀掉：' + str(seen['cmd'])
    assert '4321' in note                       # 收口这件事必须留下可读的一句话


def test_没有pid时不许乱杀(monkeypatch):
    from duanxian import proc
    called = []
    monkeypatch.setattr(proc.subprocess, 'run', lambda *a, **k: called.append(a))
    assert '什么都没收' in proc.kill_tree(0)
    assert called == []


# ---------------------------------------------------------------- 收盘即时链的超时

def test_子作业超时按进程树收并退124(monkeypatch):
    from ingest.jobs import daily_close_sync as dcs

    class _P:
        pid = 777

        def wait(self, timeout=None):
            assert timeout == 60                  # 上限真的传进了 wait，不是只打印给人看
            raise subprocess.TimeoutExpired(['x'], timeout)

    killed = []
    monkeypatch.setattr(dcs.subprocess, 'Popen', lambda *a, **k: _P())
    monkeypatch.setattr('duanxian.proc.kill_tree',
                        lambda pid: killed.append(pid) or '已收 %s' % pid)
    rc = dcs._run(['-m', 'ingest.jobs.c1_day'], what='入库 · 测试', timeout=60)
    assert rc == 124, '超时不能交回 0（那会被读成"这一步成了"）'
    assert killed == [777]


def test_正常退出码照原样交回(monkeypatch):
    from ingest.jobs import daily_close_sync as dcs

    class _P:
        pid = 1

        def wait(self, timeout=None):
            return 3

    monkeypatch.setattr(dcs.subprocess, 'Popen', lambda *a, **k: _P())
    assert dcs._run(['-m', 'x'], what='T', timeout=30) == 3


def test_桌面那条腿的超时比默认值长(monkeypatch):
    """p0/run_agent 自己就要等分钟线 7′41″ + 转档 + 每步重试，
    给它默认 20 分钟会把合法的一轮掐在半截。"""
    from ingest.jobs import daily_close_sync as dcs
    assert dcs.GUI_TIMEOUT_SEC > dcs.STEP_TIMEOUT_SEC


# ---------------------------------------------------------------- 加工链的每步超时

def test_因子步的超时容得下实测耗时():
    from ingest.jobs import e1_fill_all as e1
    # 2026-09-30 实跑 d1_indicators --recent 5 = 1002 秒；上限必须明显高于它，
    # 否则这条加固本身会变成新的故障源。
    assert e1.step_timeout('indicators') > 3000
    assert e1.step_timeout('scores') > 1002
    assert e1.step_timeout('quality') == 600
    assert e1.step_timeout('没登记的步') == e1.STEP_TIMEOUT_SEC   # 默认值≠没有限制


def test_看门狗到点把步判成超时(monkeypatch):
    """`read stdout` 的循环会阻塞，`proc.wait(timeout=)` 对"一行都不吐的卡死"根本到不了，
    所以用的是计时器 + 进程树收口。这里演的是：计时器一响，退出码必须是 124 且
    归因里带"超时"（`classify_failure` 靠这个关键字，缺了就会被归成别的病因）。"""
    from ingest.jobs import e1_fill_all as e1

    class _Out:
        def __iter__(self):
            return iter([])

    class _P:
        pid = 9
        stdout = _Out()

        def wait(self):
            return 0

    fired = {'fn': None}

    class _Timer:
        def __init__(self, secs, fn):
            fired['fn'] = fn
            fired['secs'] = secs

        def start(self):
            fired['fn']()               # 立刻命中，模拟卡死

        def cancel(self):
            pass

        daemon = True

    monkeypatch.setattr(e1.subprocess, 'Popen', lambda *a, **k: _P())
    monkeypatch.setattr(e1.threading, 'Timer', _Timer)
    monkeypatch.setattr('duanxian.proc.kill_tree', lambda pid: '收掉 %s' % pid)
    rc, tail = e1.run_script('ingest.jobs.d1_indicators', '--recent', '5', timeout=120)
    assert rc == 124
    assert '超时' in tail, tail
    assert fired['secs'] == 120
    assert e1.classify_failure('indicators', rc, tail) == '超时'


# ---------------------------------------------------------------- 通达信只读探测

def test_探测判的是能不能取数不是进程在不在(monkeypatch):
    import server
    from app.tdx_client import watchman

    monkeypatch.setattr(watchman, 'probe',
                        lambda: {'running': True, 'pid': 1, 'login_box': True,
                                 'minimized': False, 'sync': None, 'window': '通达信'})
    ok, detail = server._tdx_probe_round()
    assert ok is False, '开着但停在登录框上 = TQ 那条腿取不到数，不能报成"能取数"'
    assert '登录框' in detail['reason']

    monkeypatch.setattr(watchman, 'probe', lambda: {'running': False})
    ok, detail = server._tdx_probe_round()
    assert ok is False and 'vipdoc' in detail['reason']


def test_探测本身坏了也出声不假装正常(monkeypatch):
    import server
    from app.tdx_client import watchman

    def boom():
        raise OSError('枚举窗口被拒')

    monkeypatch.setattr(watchman, 'probe', boom)
    ok, detail = server._tdx_probe_round()
    assert ok is False and '探测本身失败' in detail['reason']


def test_pytest里不起这条线程(monkeypatch):
    """常驻循环在测试里必须不真起：它会枚举真窗口并把 state.json 写脏
    （`conftest` 拦的是 socket，拦不住 `EnumWindows`）。"""
    import server
    started = []
    monkeypatch.setattr(server.threading, 'Thread',
                        lambda *a, **k: type('T', (), {'start': lambda s: started.append(1)}))
    monkeypatch.setenv('PYTEST_CURRENT_TEST', 'x')
    monkeypatch.setattr(server, '_tdx_watch_thread_started', False)
    server._start_tdx_watch()
    assert started == []
    monkeypatch.delenv('PYTEST_CURRENT_TEST')
    monkeypatch.setenv('VIBE_TDX_WATCH', '0')
    monkeypatch.setattr(server, '_tdx_watch_thread_started', False)
    server._start_tdx_watch()
    assert started == []                          # 显式关也不起


def test_监控列进常驻循环清单读数():
    """`host_loops` 是判"重启生效没有"的唯一依据；没登记就等于"缺了也没人报"。"""
    from vr import jobsboard
    assert 'tdx-watch' in jobsboard.HOST_LOOPS


def test_没有探测记录时界面读到的是null不是默认在跑(monkeypatch):
    """2026-10-06 之前就是这种状态：监控代码存在、没人调用、页面上一片正常。"""
    from vr import jobsboard
    from duanxian import jobs
    monkeypatch.setattr(jobs, 'state', lambda: {})
    body = jobsboard.chain_status()
    tdx = body['tdx']
    assert tdx['running'] is None and tdx['ok'] is None
    assert tdx['loop_alive'] is False            # 线程没起就要报没起
    assert tdx['heal'].startswith('watchman.heal 未接')   # 那条老路（抢前台发键鼠）仍没接
    assert '③b' in tdx['heal']                          # 但"没人拉起"已经不是事实：链会自己起
    # 待人工清单必须在这个端点上看得见（用户 10-06 的条件："哪些没执行的我手动执行下"）
    assert body['tdx_trail']['pending'] == []           # 还没动过界面 = 空清单，不是 null 也不是报错
    assert body['tdx_trail']['path'].endswith('gui_trail.json')


def test_段超时容得下单步预算():
    """加固不能自己变成新故障源：**段超时必须大于段内任何一步的预算**。
    否则某一步正按合法预算慢跑（`d1_indicators --recent 5` 实测 1002 秒），
    宿主先到点掐掉整段、按 hold 从头再跑一遍 —— 那既浪费一轮，又把"正在正常完成"
    记成一次失败。09-30 那晚 `fill_all` 的段超时是 5400，而因子一步就要跑到 1000+，
    窗口拉长到 10 场就会撞上；这条断言钉住的是这两个数之间的顺序关系。
    """
    from duanxian import jobs as jstore
    from ingest.jobs import daily_close_sync as dcs
    from ingest.jobs import e1_fill_all as e1

    assert jstore.CLOSE_CHAIN["daily_close"]["timeout"] > max(
        dcs.STEP_TIMEOUT_SEC, dcs.TQ_TIMEOUT_SEC, dcs.GUI_TIMEOUT_SEC)
    assert jstore.CLOSE_CHAIN["fill_all"]["timeout"] > max(e1.STEP_TIMEOUTS.values())
    # 每一步都要有预算：`STEP_TIMEOUT_SEC` 是兜底，不是"不设限"
    assert e1.STEP_TIMEOUT_SEC > 0 and dcs.STEP_TIMEOUT_SEC > 0


def _popen_fakes(monkeypatch, killed, lines=('第一行\n', '第二行\n'), hang=False):
    """打 `_spawn_stage` 内部那一层：它现在用 `Popen`，所以这里假的是 `Popen`。"""
    class _P:
        pid = 5151

        @property
        def stdout(self):
            return iter(list(lines))

        def wait(self, timeout=None):
            if hang:
                raise subprocess.TimeoutExpired(['x'], timeout)
            return 0

    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: _P())
    monkeypatch.setattr('duanxian.proc.kill_tree',
                        lambda pid: killed.append(pid) or 'taskkill /PID %s /T /F → 0' % pid)


def test_pytest进程里硬挡真实收盘链(monkeypatch):
    """**这条就是 2026-10-06 那次越界的回归**：执行体改成 `Popen` 之后，宿主测试里
    假的是 `subprocess.run`，于是测试真的起了 `daily_close_sync` → `p0_tdx_predownload`
    → 在用户桌面上把通达信启动了（`TdxW.exe` pid 14800 / 17:39:19）。
    现在两道都在：测试打 `_spawn_stage` 这个洞，而**就算没人打洞**，pytest 进程里也不起真链。
    """
    from duanxian import jobs as jstore
    monkeypatch.setenv('PYTEST_CURRENT_TEST', 'x')          # 本来就有，这里写死免得依赖跑法
    started = []
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: started.append(a))
    rc, tail = jstore._spawn_stage('ingest.jobs.daily_close_sync', 30)
    assert started == [], '测试进程里起了真实子进程'
    assert rc == 127 and '_spawn_stage' in tail


def test_宿主超时按进程树收孤儿(monkeypatch):
    """超时不能只杀段脚本：里面那层（`c1/c16/p0/run_agent`）会变孤儿继续写库。"""
    from duanxian import jobs as jstore
    killed: list[int] = []
    _popen_fakes(monkeypatch, killed, hang=True)
    monkeypatch.delenv('PYTEST_CURRENT_TEST')      # 绕开上一测那道闸，单独验收树这件事
    rc, tail = jstore._spawn_stage('ingest.jobs.c1_day', 60)
    assert rc == 124
    assert killed == [5151], '超时必须收到整棵树，不能只杀段脚本'
    assert '进程树' in tail


def test_宿主超时不静默只留一个退出码(monkeypatch):
    """`reason` 是页面上唯一能读到的话：要写清是被谁收的、以及子作业最后做到哪一步。"""
    from duanxian import jobs as jstore
    killed: list[int] = []
    _popen_fakes(monkeypatch, killed, lines=('=== 1. 取数 ===\n',), hang=True)
    monkeypatch.delenv('PYTEST_CURRENT_TEST')
    rc, tail = jstore._spawn_stage('ingest.jobs.c1_day', 60)
    assert 'taskkill' in tail and '取数' in tail, tail

    recorded = []
    monkeypatch.setattr(jstore, '_spawn_stage',
                        lambda module, timeout: (124, '超过 60 秒未完成 → 已按进程树收掉'))
    monkeypatch.setattr(jstore, 'record',
                        lambda name, *, ok, detail=None: recorded.append((name, ok, detail)))
    out = jstore.exec_close_stage('daily_close', {"attempt": 2})
    assert out["rc"] == 124 and out["verdict"] == "hold"
    assert out["reason"] and "进程树" in out["reason"]
    assert recorded and recorded[0][1] is False and recorded[0][2]["attempt"] == 2


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))


# ------------------------------------------------ 收掉赖着不走的 TPyth 同步进程（用户点名）

def _reap_env(monkeypatch, pids, ages, busy=None, growing=False, after_min=40):
    import server
    from app.tdx_client import win32
    from duanxian import jobs as jstore

    monkeypatch.setattr(win32, 'pids_by_prefix', lambda _p: list(pids))
    monkeypatch.setattr(server, '_sync_process_age', lambda pid: ages.get(pid))
    monkeypatch.setattr(server, '_vipdoc_still_growing', lambda **k: growing)
    monkeypatch.setattr(jstore, 'stage_busy', lambda: busy)
    monkeypatch.setattr(jstore, 'settings',
                        lambda name, defaults: ({'enabled': True,
                                                 'sync_reap_after_min': after_min}, []))
    killed = []
    monkeypatch.setattr('duanxian.proc.kill_tree',
                        lambda pid: killed.append(pid) or 'taskkill /PID %s /T /F → 0' % pid)
    recorded = []
    monkeypatch.setattr(jstore, 'record',
                        lambda name, *, ok, detail=None: recorded.append((name, ok)))
    return killed, recorded, server


def test_同步进程活太久且文件不再往前写就收掉(monkeypatch):
    """用户原话：「下载完之后关闭这个窗口……`TPyth` 那个进程，结束了你要帮我关闭哦，
    或者定时去关闭，时间设置长一点」。这里是"定时"那一条：40 分钟一档，跟着探测循环走。"""
    killed, recorded, server = _reap_env(
        monkeypatch, [('TPyth.exe', 4242)], {4242: 41 * 60.0})
    out = server._reap_stale_sync()
    assert killed == [4242]
    assert out['killed'][0]['age_seconds'] == 2460
    assert 'TdxW' in out['reason'] and '未动' in out['reason']   # 说清没碰主客户端
    assert ('tdx_sync_reap', True) in recorded


def test_链正在跑就不收那个进程(monkeypatch):
    """那个进程就是链自己叫起来的，收了等于把正在下的盘后数据掐断。"""
    killed, recorded, server = _reap_env(
        monkeypatch, [('TPyth.exe', 1)], {1: 99 * 60.0}, busy='daily_close')
    assert server._reap_stale_sync()['reason'].startswith('收盘链 daily_close 正在跑')
    assert killed == [] and recorded == []


def test_文件还在往前写就不收(monkeypatch):
    """光看年龄会误杀正在下载的那天 —— 40 分钟是上限，不是"到点必杀"。"""
    killed, _rec, server = _reap_env(
        monkeypatch, [('TPython.exe', 7)], {7: 90 * 60.0}, growing=True)
    assert '还在往前写' in server._reap_stale_sync()['reason']
    assert killed == []


def test_判不了还在不在写就不动手(monkeypatch):
    """`None` 是"读不到"，读不到时**宁可不杀**（与交易日判不了不许当非交易日同一条规矩）。"""
    killed, _rec, server = _reap_env(
        monkeypatch, [('TPyth.exe', 7)], {7: 90 * 60.0}, growing=None)
    assert killed == []


def test_没到时间阈值不收(monkeypatch):
    killed, _rec, server = _reap_env(monkeypatch, [('TPyth.exe', 9)], {9: 60.0})
    assert '没到 40 分钟' in server._reap_stale_sync()['reason']
    assert killed == []


def test_阈值设成零就是明确关掉这件事(monkeypatch):
    killed, _rec, server = _reap_env(
        monkeypatch, [('TPyth.exe', 9)], {9: 10 * 3600.0}, after_min=0)
    assert server._reap_stale_sync() is None and killed == []


def test_读不到进程创建时间就不杀(monkeypatch):
    killed, _rec, server = _reap_env(monkeypatch, [('TPyth.exe', 9)], {9: None})
    assert killed == [] and server._reap_stale_sync()['reason'].startswith('在跑但没到')


# ---------------------------------- 下载阶梯（用户 10-06 原话：10 分钟查、两次不全就全重启）

def _ladder_env(monkeypatch, running=(True, 'TPython.exe(4242)'), complete=(True, '都到 2026-09-30'),
                busy=None, auto_restart=True, clock=None, closed=None, trace=None):
    import server
    from app.tdx_client import locator
    from duanxian import jobs as jstore

    server._ladder.update(target=None, checks=0, next_at=0.0)
    monkeypatch.setattr(locator, 'sync_running', lambda: tuple(running))
    monkeypatch.setattr(jstore, 'stage_busy', lambda: busy)
    monkeypatch.setattr(jstore, 'settings',
                        lambda name, defaults: ({'enabled': True, 'sync_reap_after_min': 40,
                                                 'auto_restart': auto_restart}, []))
    monkeypatch.setattr(server, '_vipdoc_download_complete', lambda _t: complete)
    killed, recorded, restarted = [], [], []
    closed = closed if closed is not None else []
    trace = trace if trace is not None else []
    monkeypatch.setattr('duanxian.proc.kill_tree',
                        lambda pid: killed.append(pid) or 'taskkill /PID %s /T /F → 0' % pid)
    monkeypatch.setattr(jstore, 'record',
                        lambda name, *, ok, detail=None: recorded.append((name, ok)))
    monkeypatch.setattr(server, '_restart_tdx_for_retry',
                        lambda reason: restarted.append(reason) or {'restarted': True,
                                                                    'reason': '已全重启'})
    # **关窗那一段必须打桩**：真实现会 `win32.minimize()` 用户桌面上的通达信主窗口
    # （`s8_finish.cleanup` 里除了关窗还有最小化，而最小化不在 close_dialog 的 pytest 闸后面）。
    def fake_close(t, *, force=True):
        closed.append(str(t))
        trace.append('close')
        return '对话框已关；已最小化'
    monkeypatch.setattr(server, '_close_windows_now', fake_close)
    def fake_reap(*, force=False):
        trace.append('reap')
        killed.append('force' if force else 'age')
        return {'killed': list(killed), 'reason': '收了'}
    monkeypatch.setattr(server, '_reap_stale_sync', fake_reap)
    tick = {'t': clock if clock is not None else 1000.0}
    monkeypatch.setattr(server.time, 'monotonic', lambda: tick['t'])
    return server, killed, recorded, restarted, tick


def test_没有下载在跑时阶梯什么都不做(monkeypatch):
    server, killed, recorded, restarted, _t = _ladder_env(
        monkeypatch, running=(False, '没有 TPyth* 进程'))
    assert server._download_ladder_round() is None
    assert (killed, recorded, restarted) == ([], [], [])


def test_刚看到下载在跑只排第一档不立刻判(monkeypatch):
    server, killed, _rec, _re, _t = _ladder_env(monkeypatch)
    out = server._download_ladder_round()
    assert out['action'] == 'watching' and out['check'] == 0
    assert killed == []
    assert out['reason'].startswith('看到 TPython.exe')


def test_第一档数据全了就收进程(monkeypatch):
    server, killed, recorded, restarted, tick = _ladder_env(monkeypatch)
    server._download_ladder_round()                      # 建轮次
    tick['t'] += server.TDX_LADDER_CHECK_SEC + 1         # 10 分钟到
    out = server._download_ladder_round()
    assert out['action'] == 'closed' and out['check'] == 1
    assert killed == ['force'], '必须走 force：数据已判全，不该再等年龄阈值'
    assert restarted == [] and ('tdx_sync_ladder', True) in recorded


def test_判全之后先关窗再收进程并留下待人工痕迹(monkeypatch, tmp_path):
    """用户 10-06 两句合起来的意思：**"下载好了就把弹窗取消"** + **"关了进程也要关掉窗口"**。

    顺序是有原因的：「盘后数据下载」是 `TdxW` 的对话框，温和点掉之后 `TPyth*` 常常自己就退了；
    先杀进程窗口会一直挂着没人收，下一轮 s5 再点就是两层叠窗（那正是用户截图里的形状）。
    """
    from duanxian import gui_trail
    monkeypatch.setattr(gui_trail, 'data_path', lambda *p: str(tmp_path / 'trail'))
    closed, trace = [], []
    server, killed, recorded, _r, tick = _ladder_env(monkeypatch, closed=closed, trace=trace)
    server._download_ladder_round()
    tick['t'] += server.TDX_LADDER_CHECK_SEC + 1
    out = server._download_ladder_round()
    assert out['action'] == 'closed'
    assert trace[:2] == ['close', 'reap'], '关窗排在收进程后面 = 窗口会一直挂着'
    assert closed and out['closed'] == '对话框已关；已最小化'
    pend = gui_trail.pending()
    assert pend == []                                    # 做干净的不该出现在待人工清单
    assert len(gui_trail.items(limit=10)) == 2           # 但全量要留：关窗 + 收进程
    assert {x['step'] for x in gui_trail.items(limit=10)} == {'s8_finish', 'reap_sync'}


def test_关窗那段在pytest里硬挡真桌面(monkeypatch):
    """`_close_windows_now` 是唯一发点击/最小化的洞：**没打桩就必须挡住**。

    这条测试自己就是那条红线：它不调 monkeypatch 就真调 `_close_windows_now`，
    挡不住的话会把用户桌面上的通达信最小化掉（10-06 已经出过一次"测试真起通达信"）。
    """
    import os

    import server
    assert os.environ.get('PYTEST_CURRENT_TEST'), '这条测试必须在 pytest 里跑'
    notes = server._close_windows_now(__import__('datetime').date(2026, 9, 30))
    assert 'PYTEST 硬挡' in notes


def test_第一档没全就再等十分钟查第二次(monkeypatch):
    server, killed, _rec, restarted, tick = _ladder_env(
        monkeypatch, complete=(False, '5 分钟线还没到那天'))
    server._download_ladder_round()
    tick['t'] += server.TDX_LADDER_CHECK_SEC + 1
    out = server._download_ladder_round()
    assert out['action'] == 'waiting' and '再等 10 分钟查第二次' in out['reason']
    assert killed == [] and restarted == []
    tick['t'] += server.TDX_LADDER_CHECK_SEC + 1
    out2 = server._download_ladder_round()
    assert out2['check'] == 2 and out2['action'] == 'restarted'
    assert len(restarted) == 1                            # 两档都不全 → 全重启通达信


def test_链在跑时阶梯不抢客户端(monkeypatch):
    server, killed, _rec, restarted, tick = _ladder_env(monkeypatch, busy='daily_close')
    server._download_ladder_round()
    tick['t'] += 99999
    assert server._download_ladder_round() is None
    assert killed == [] and restarted == []


def test_自动重启可以明确关掉只留查与关进程(monkeypatch):
    """`auto_restart=false` 时两档都不全也**不去动客户端**，但要说清为什么没动。"""
    server, killed, _rec, restarted, tick = _ladder_env(
        monkeypatch, complete=(False, '覆盖只有 62%'), auto_restart=False)
    monkeypatch.setattr(server, '_restart_tdx_for_retry', lambda r: restarted.append(r) or
                        {'restarted': False, 'reason': 'auto_restart=false'})
    server._download_ladder_round()
    for _ in range(2):
        tick['t'] += server.TDX_LADDER_CHECK_SEC + 1
        out = server._download_ladder_round()
    assert out['action'] == 'restart-blocked'


def test_全重启那一步自己有两道闸(monkeypatch):
    """真去重启的那段：链在跑不许抢；`auto_restart=false` 不许动键盘。"""
    import server
    from duanxian import jobs as jstore
    started = []
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: started.append(a))
    monkeypatch.setattr(jstore, 'stage_busy', lambda: 'daily_close')
    out = server._restart_tdx_for_retry('覆盖只有 62%')
    assert out['restarted'] is False and started == [] and '正在跑' in out['reason']
    monkeypatch.setattr(jstore, 'stage_busy', lambda: None)
    monkeypatch.setattr(jstore, 'settings',
                        lambda name, defaults: ({'auto_restart': False}, []))
    out = server._restart_tdx_for_retry('覆盖只有 62%')
    assert out['restarted'] is False and started == [] and 'auto_restart' in out['reason']


def test_数据全不全判的是日期加整目录条数不是窗口文字(monkeypatch, tmp_path):
    """钉住"半截冒充全量"：09-23 那次分钟线只进了北交所 351 只，日期看着齐、量级差 27 倍。

    2026-10-06 这道门从"抽样 ≥90%"换成 `duanxian/completeness.py` 的**动态条数规则**
    （数整个 vipdoc，判 `≥ 前第 2 个交易日`），所以这里打桩的也是那份实现：
    日期由假 `Readiness` 给，条数由假 `vipdoc_counts` 给，登记表必须在 tmp 里
    —— 用真表测出来的绿灯和代码无关，还会把真表写脏。
    """
    import datetime as _dt

    import server
    from app.tdx_client import readiness
    from duanxian import completeness

    class _P:
        def __init__(self, ok): self.ok, self.stale_days, self.note = ok, 0 if ok else 1, ''

    class _V:
        def __init__(self, ok, probes=None): self.ok, self.probes = ok, probes or []
        def report(self): return ''

    class _H:
        def __init__(self, vipdoc=None, *, extra_samples=0, seed=None): pass
        def day_ready(self, t=None, *, markets=None): return _V(True)
        def index_ready(self, t=None): return _V(True)
        def minute_ready(self, t=None, *, period=1, markets=None): return _V(True)

    monkeypatch.setattr(readiness, 'Readiness', _H)
    monkeypatch.setattr(completeness, 'data_path', lambda *p: str(tmp_path / 'c'))
    DAY = _dt.date(2026, 9, 30)
    # 基线用**各口径自己的实测数**（2026-10-07 逐日量 vipdoc 的结果）：
    # `.day` 09-28/09-29 = 9,508/9,501（→ 09-30 的 9,490 只差 11 条，在 30 以内）；
    # 分钟线 09-28/09-29 = 9,440/9,423（→ 09-30 的 9,398 差 25 条，在分钟类 200 以内）。
    # 三个口径共用一个数会把 `.day` 判成"少 41 条"，那是 fixture 造出来的假不全。
    measured = {'vipdoc_day': (9508, 9501), 'vipdoc_lc1': (9440, 9423), 'vipdoc_lc5': (9440, 9423)}
    for kind, (n28, n29) in measured.items():
        completeness.record(kind, '2026-09-28', n28, source='test')
        completeness.record(kind, '2026-09-29', n29, source='test')

    counts = {'vipdoc_day': 351, 'vipdoc_lc1': 351, 'vipdoc_lc5': 351}
    monkeypatch.setattr(completeness, 'vipdoc_counts', lambda day, **kw: dict(counts))
    ok, why = server._vipdoc_download_complete(DAY)
    assert ok is False and '351' in why, why                 # 日期齐、条数只有 3.7%
    assert '基线 2026-09-29（隔 1 场）' in why, why          # 2026-10-07 起基线就是前一天
    assert str(DAY) not in [str(x) for x in completeness.history('vipdoc_day')]  # 不进基线池

    counts.update({'vipdoc_day': 9490, 'vipdoc_lc1': 9398, 'vipdoc_lc5': 9398})
    ok, why = server._vipdoc_download_complete(DAY)
    assert ok is True and '整目录' in why, why

    class _H3(_H):
        def minute_ready(self, t=None, *, period=1, markets=None): return _V(False)

    monkeypatch.setattr(readiness, 'Readiness', _H3)
    ok, why = server._vipdoc_download_complete(DAY)
    assert ok is False and '5 分钟线' in why, why
