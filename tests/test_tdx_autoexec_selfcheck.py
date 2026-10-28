# -*- coding: utf-8 -*-
"""数据获取这一层的自动执行自查回归（2026-10-06）。

配套判据见 `docs/系统配置工作指南/06-自动执行机制自查表.md`（唯一一份，别另写）。
这里只钉**我这次真改的东西**，全部离线：不碰界面、不连库、不起线程。

1. `watchman` 的两道闸分开：只读探测默认开、自愈默认关；旧形状是**一个 `enabled=False`
   把两者一起关掉** —— 代码在、没人跑，用户问"有没有监控"时最坏的答案。
2. `_tick` 的先后：先探再判要不要动手；且 `enabled=False` 时**一次 heal 都不许调**。
3. `s8.cleanup()` 吞异常之后必须留可读记录（对话框没关掉会让下一轮 s5 叠两层窗）。
4. p0 的"下载到一半没成功"三分类里 B（还在推进）与 A（死了）不许混：
   混了就会去重启客户端，打断一次本来会成功的下载（09-28 实测链 16:31 判停、
   通达信 17:2x 才落完）。
5. 起作业的路径：本项目没有 `scripts/<ingest|ops|compute|migrate>/`，
   运行期 print 里的旧路径会被人和智能体照抄（09-29 就是这么把整条加工链跳过的）。
6. p0 的**自限预算**：三步上限相加 6600s/轮 > 上级最紧的 2100s，不自限就是被
   `kill_tree` 掉、没有出口码；同时 C 类（要人来）与"快速失败"都不许在几十秒内
   把自己重试完 —— 链的下一档（+25 分钟）要能等到我们。
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import time

import pytest

from app.tdx_client import watchman
from app.tdx_client.steps import s8_finish
from ingest.jobs import c10_catchup as c10
from ingest.jobs import p0_tdx_predownload as p0


# ---------------------------------------------------------------- watchman 两道闸

def test_默认值是探测开自愈关():
    assert watchman._DEFAULTS['probe'] is True
    assert watchman._DEFAULTS['enabled'] is False
    assert watchman._DEFAULTS['auto_start'] is True      # 只在 enabled 为真时才起作用


def test_库里没配probe键时探测仍然算开着(monkeypatch):
    """`config.setting` 里现在只有 `tdx_watch.enabled=false`，**没有 `probe` 这一行**。
    如果探测还跟着 `enabled` 走，库里那个 false 就会把监控一起关掉（旧 bug 的形状）。"""
    from app.db.repo import registry as reg

    stored = {'tdx_watch.enabled': False, 'tdx_watch.auto_start': True,
              'tdx_watch.interval_min': 10, 'tdx_watch.minimize': True,
              'tdx_watch.restart_at': ''}
    monkeypatch.setattr(reg, 'setting', lambda k, *a, **kw: stored.get(k))
    conf = watchman.load_conf()
    assert conf['probe'] is True, '库里没配 probe 就该按默认开，不许被 enabled 连着关掉'
    assert conf['enabled'] is False


def test_库里读不到任何配置时探测也算开着(monkeypatch):
    """库挂了这个看护还在（只读枚举窗口，本来就不需要库）。"""
    from app.db.repo import registry as reg

    def boom(*a, **kw):
        raise RuntimeError('库没连上')

    monkeypatch.setattr(reg, 'setting', boom)
    conf = watchman.load_conf()
    assert conf['enabled'] is False          # 自愈：读不到就按"关"
    assert conf['probe'] is True             # 探测：读不到也照跑


def test_tick_先探测后决定动手且不开自愈(monkeypatch):
    calls = []
    monkeypatch.setattr(watchman, 'load_conf', lambda: dict(
        watchman._DEFAULTS, interval_min=10))
    monkeypatch.setattr(watchman, 'probe', lambda: {
        'at': '2026-10-06 17:18:42', 'running': False, 'pid': None, 'window': None,
        'minimized': None, 'login_box': False, 'sync': '没有 TPyth* 进程'})
    monkeypatch.setattr(watchman, '_load_state', lambda: {})
    monkeypatch.setattr(watchman, '_save_state', lambda st: calls.append(('save', st)))
    import duanxian.jobs as jobs
    monkeypatch.setattr(jobs, 'record',
                        lambda *a, **kw: calls.append(('jobs.record', a[0])))
    monkeypatch.setattr(watchman, 'heal',
                        lambda *a, **kw: calls.append(('heal', a, kw)))
    watchman._tick()
    kinds = [c[0] for c in calls]
    assert 'save' in kinds, '探测明细至少要有可读记录，不然"看没看见"无从谈起'
    assert 'heal' not in kinds, 'enabled=False 时一次自愈都不许调（它会抢前台发真实键鼠）'
    assert 'jobs.record' not in kinds,         'tdx_watch 的记账者是宿主（server.py::_tdx_watch_scheduler）；这里再写一份就是两个记账者各判各的 ok'


def test_本模块不再自己往jobs状态里写第二个tdx_watch():
    """一个信号两个写者 = 谁后写谁覆盖，而两边的 ok 语义还不一样。"""
    import inspect
    src = inspect.getsource(watchman)
    assert 'jobs.record' not in src, '记账回到 watchman 里了，会与宿主的 tdx_watch 抢键'
    assert 'def probe_once' not in src


def test_明确关掉探测时连窗口都不枚举(monkeypatch):
    hits = []
    monkeypatch.setattr(watchman, 'load_conf', lambda: dict(
        watchman._DEFAULTS, probe=False))
    monkeypatch.setattr(watchman, 'probe', lambda: hits.append('probe') or {})
    watchman._tick()
    assert hits == []


# ---------------------------------------------------------------- 关窗要复验并出声

def test_对话框没关掉要记一次_not_ok(monkeypatch):
    """`cleanup()` 吞异常是设计（不能盖掉真失败原因），但**吞了之后必须留可读记录**：
    `TQ数据设置` 还摊着，下一轮 s5 再点就是两层窗口，日志上看着像"源坏了"。"""
    recs = []
    monkeypatch.setattr(s8_finish, 'close_dialog',
                        lambda pid=None, **kw: ['点了 关闭 按钮', '对话框仍在'])
    monkeypatch.setattr(s8_finish.locator, 'main_window', lambda: None)
    import duanxian.jobs as jobs
    monkeypatch.setattr(jobs, 'record',
                        lambda name, ok, detail=None: recs.append((name, ok, detail)))
    out = s8_finish.cleanup()
    assert '对话框仍在' in out
    assert recs and recs[0][0] == 'tdx_close_verify' and recs[0][1] is False
    assert '对话框仍在' in recs[0][2]['reason']       # 归因走 reason 键（record 只认这个）


def test_收干净了就不记别把最后一次异常淹掉(monkeypatch):
    """每轮都写一条会让"最后一次没关掉"被心跳淹掉，而页面要看的正是那一次。"""
    recs = []
    monkeypatch.setattr(s8_finish, 'close_dialog',
                        lambda pid=None, **kw: ['对话框已关'])
    monkeypatch.setattr(s8_finish, '_minimized', lambda w: True)

    class _W:
        hwnd = 1
        rect = 'r'

    monkeypatch.setattr(s8_finish.locator, 'main_window', lambda: _W())
    import duanxian.jobs as jobs
    monkeypatch.setattr(jobs, 'record',
                        lambda *a, **kw: recs.append((a, kw)))
    assert '已是最小化' in s8_finish.cleanup()
    assert recs == []


def test_收尾自己抛异常也要留下记录不许静默(monkeypatch):
    recs = []
    monkeypatch.setattr(s8_finish, 'close_dialog',
                        lambda pid=None, **kw: (_ for _ in ()).throw(
                            RuntimeError('枚举窗口失败')))
    import duanxian.jobs as jobs
    monkeypatch.setattr(jobs, 'record',
                        lambda name, ok, detail=None: recs.append((name, ok, detail)))
    out = s8_finish.cleanup()
    assert '收尾时出错' in out and recs and recs[0][1] is False


def test_记账坏了不许把收尾带崩(monkeypatch):
    """`cleanup()` 是在 `finally` 里跑的：它自己抛了就等于把真失败原因盖掉。"""
    import duanxian.jobs as jobs

    def boom(*a, **kw):
        raise RuntimeError('state.json 写不进去')

    monkeypatch.setattr(jobs, 'record', boom)
    monkeypatch.setattr(s8_finish, 'close_dialog',
                        lambda pid=None, **kw: ['对话框仍在'])
    monkeypatch.setattr(s8_finish.locator, 'main_window', lambda: None)
    assert '对话框仍在' in s8_finish.cleanup()          # 不抛，说明照留


# ---------------------------------------------------------------- p0 的三分类

def test_前沿在涨就是晚到不是死了():
    b = {'max_date': '2026-09-24', 'downloading': False}
    a = {'max_date': '2026-09-30', 'downloading': False}
    assert p0.still_moving(b, a) is True               # 覆盖前沿推进 → 等，别重启


def test_下载子进程还在也算晚到():
    same = {'max_date': '2026-09-30', 'downloading': True}
    assert p0.still_moving(same, same) is True


def test_前沿不动又没在下载才是真死了():
    f = {'max_date': '2026-09-24', 'downloading': False}
    assert p0.still_moving(f, f) is False


def test_没有任何日期文件时不许判成在推进():
    empty = {'max_date': None, 'downloading': False}
    assert p0.still_moving(empty, empty) is False


def test_晚到的出口码是4且文档写明对齐hold():
    """4 = "晚到、按节奏等"。总协调侧的 hold 用的就是 4；
    写成 1 会被读成"死了"，写成 2 会升级成"要人来"。"""
    src = pathlib.Path(p0.__file__).read_text(encoding='utf-8')
    assert re.search(r'\n    4[^\n]*晚到', src), '出口码 4 的语义没写进模块头'
    assert 'return 4' in src
    assert 'still_moving' in src and 'frontier()' in src


def test_卡死判据用无输出时长而不是整步总超时():
    """真卡住时状态栏文字不变，总超时要等满一小时才发现。"""
    assert p0.STALL_LIMIT <= 600, f'STALL_LIMIT={p0.STALL_LIMIT}，被放宽到不如没有'
    for _name, _what, _keys, to in p0.PHASES:
        assert to and to > 0, '每一步都必须有自己的总上限（防无限等）'


# ---------------------------------------------------------------- 起作业的路径

_OLD_PATH = re.compile(r'(scripts[\\/]{1,2}(?:ingest|ops|compute|migrate)[\\/]{1,2}\w+\.py)')

MINE = ['ingest/jobs/c12_tq_day.py', 'ingest/jobs/c11_fundflow.py',
        'ingest/jobs/b6_reconcile.py', 'ingest/jobs/p0_tdx_predownload.py',
        'ingest/jobs/c13_ths_block.py', 'ingest/jobs/c14_ths_watchlist.py',
        'ingest/jobs/c10_catchup.py', 'app/tdx_client/run_agent.py',
        # 10-06 又扫出四处同样的形状：用法段/运行期 print/SQL 注释里写着参考仓库的目录
        'ingest/jobs/c20_ths_reference.py', 'ingest/jobs/d4_ignition.py',
        'ingest/jobs/qd0_st_period.py', 'app/db/sql/15_fix_sector_type.sql',
        'app/db/sql/13_qd_jobs.sql',
        'docs/数据获取工作指南/01-数据源分层与获取方案.md',
        'docs/数据获取工作指南/02-每日流程runbook.md',
        'docs/数据获取工作指南/02-盘后数据下载与入库.md',
        'docs/数据获取工作指南/03-故障排查.md',
        'docs/数据获取工作指南/08-FinanceTool×YuanShan去重矩阵.md']


@pytest.mark.parametrize('rel', MINE)
def test_不给人和智能体留下抄不得的旧路径(rel):
    """**只管 print/命令构造**，不管解释历史的那句话。

    09-29 那次 `c10` 用 `scripts/ops/p0_...py` 起下载，每步 `can't open file`、
    退出码 2 长得像"下载没成"，实际一条命令都没执行，整条加工链被连带跳过。
    运行期 print 里的旧路径和文档串里的用法段是同一类缺陷：抄了就中招。
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    text = (root / rel).read_text(encoding='utf-8')
    bad = []
    for i, line in enumerate(text.splitlines(), 1):
        if not _OLD_PATH.search(line):
            continue
        if '没有' in line or '旧' in line or '那种路径' in line or '本项目根本' in line:
            continue                          # 讲"为什么不能用旧路径"的句子，留着才有教训
        bad.append('%s:%d %s' % (rel, i, line.strip()[:90]))
    assert not bad, '还能被照抄的旧路径：\n' + '\n'.join(bad)


# ---------------------------------------------------------------- 判据 3：超时与收进程树

def test_卡死判据真的会收进程树而不是杀一层(monkeypatch):
    """行为级：把阈值调小，喂一个"一句话都不说"的子进程，看守护线程到底做了什么。

    为什么必须测到"收树"：`p.kill()` 只杀 p0 自己起的 `run_agent`，它再往下的东西
    会变孤儿 —— 表现是"链说超时了，通达信还在动"，那天补没补上没人说得清。
    """
    killed = []

    class _FakeP:
        pid = 7788
        returncode = None

        @property
        def stdout(self):
            # **一句话都不说**地挂着：守护线程要能在 STALL_LIMIT 之内判成卡死。
            # （曾经写成每 0.2s 挤一行空输出，结果 idle 一直被刷新，测的是"有输出"。）
            time.sleep(1.5)
            return
            yield            # 让它成为生成器：迭代时只会阻塞在上那一行

        def wait(self, timeout=None):
            return -9

        def kill(self):
            killed.append('裸kill')

    monkeypatch.setattr(p0, 'STALL_LIMIT', 0.3)
    monkeypatch.setattr(p0, 'GUARD_TICK', 0.1)
    monkeypatch.setattr(p0.proc, 'kill_tree',
                        lambda pid: killed.append(('tree', pid)) or '收了')
    monkeypatch.setattr(p0.subprocess, 'Popen', lambda *a, **kw: _FakeP())
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)   # 假 Popen 就位才过闸
    rc, note = p0.run_phase('day', ('s5_dialog',), target=dt.date(2026, 9, 30), timeout=999)
    assert rc == 1
    assert ('tree', 7788) in killed, '卡死必须按进程树收'
    assert '裸kill' not in killed, '不该再用只杀一层的 p.kill()'
    assert '卡死' in note and '进程树' in note


def test_pytest进程里p0不起真的run_agent(monkeypatch):
    """2026-10-06 越界一次：执行体从 `subprocess.run` 换成 `Popen` 后，
    宿主测试假的那个 `run` 不再被调用，于是 pytest **真的**一路起到底、
    在用户桌面上把通达信弹了出来（TdxW.exe pid 14800，17:39:19）。
    唯一能碰界面的洞是 `run_phase`，所以闸也装在这里。"""
    monkeypatch.setenv('PYTEST_CURRENT_TEST', 'x')
    started: list = []
    monkeypatch.setattr(p0.subprocess, 'Popen', lambda *a, **kw: started.append(1))
    rc, note = p0.run_phase('day', ('s5_dialog',), target=dt.date(2026, 9, 30), timeout=60)
    assert rc == 1 and started == []
    assert '硬挡' in note and 'run_phase' in note, '要指着该 monkeypatch 的那个入口说话'


def test_pytest进程里c10不起真的p0(monkeypatch, capsys):
    monkeypatch.setenv('PYTEST_CURRENT_TEST', 'x')
    started: list = []
    monkeypatch.setattr(c10.subprocess, 'Popen', lambda *a, **kw: started.append(1))
    assert c10.try_tdx_download(dt.date(2026, 9, 30), timeout=60) is False
    assert started == []
    assert '硬挡' in capsys.readouterr().out


def test_每一步都得有自己的总超时():
    """`STALL_LIMIT` 判"不出声"，整步总超时判"出声但永远跑不完"，两者不可互相替代。"""
    assert 0 < p0.STALL_LIMIT <= 600
    for name, _what, _keys, to in p0.PHASES:
        assert to and to > 0, '%s 这一步没有总上限' % name


def test_c10起p0的超时值有出处():
    """上限不是拍出来的：日线那一遍实测 10 秒、分钟线 7′41″，补数链路整体留 90 分钟。"""
    import inspect
    src = inspect.getsource(c10.try_tdx_download)
    assert 'timeout' in src and 'kill_tree' in src


# ---------------------------------------------------------------- 桌面不可用不是"再试一次"

def test_输入桌面不可用时的失败判成交人而不是可重试(monkeypatch, capsys):
    """09-30 实测：`var/stuck/20260930_16{05,56,58}_s5_dialog` 三次都是
    「四级都没打开下载对话框：D 输入桌面不可用，坐标点击发不出去」。

    这种失败**重试修不好**（桌面不会自己亮起来），而 p0 的重来会先
    `reset.kill_all()` —— 那一下把用户正开着的通达信也杀了。所以判成交人。
    """
    from app.tdx_client import run_agent as ra, win32

    monkeypatch.setattr(win32, 'session_ok', lambda: (True, '交互会话'))
    monkeypatch.setattr(win32, 'input_desktop_ok',
                        lambda: (False, 'GetCursorPos ACCESS_DENIED（测试注入）'))
    monkeypatch.setattr(ra.creds, 'has_credentials', lambda *a, **kw: True)
    monkeypatch.setattr(ra.readiness, 'expected_trade_date',
                        lambda *a, **kw: dt.date(2026, 9, 30))
    monkeypatch.setattr(ra, '_run_attempt',
                        lambda *a, **kw: (ra.EXIT_FAIL, [], '汇总'))
    rc = ra.main(['--date', '2026-09-30', '--attempts', '1'])
    out = capsys.readouterr().out
    assert rc == ra.EXIT_HANDOFF, '桌面不可用必须交人，不能留给上层去重试'
    assert '不是' in out or '交' in out or '重试' in out
    assert '输入桌面不可用' in out


def test_桌面可用时的失败还是失败不冒充交人(monkeypatch):
    """反过来也不能滥用 handoff：桌面好好的时候失败就是普通失败（该重来）。"""
    from app.tdx_client import run_agent as ra, win32

    monkeypatch.setattr(win32, 'session_ok', lambda: (True, '交互会话'))
    monkeypatch.setattr(win32, 'input_desktop_ok', lambda: (True, '可用'))
    monkeypatch.setattr(ra.creds, 'has_credentials', lambda *a, **kw: True)
    monkeypatch.setattr(ra.readiness, 'expected_trade_date',
                        lambda *a, **kw: dt.date(2026, 9, 30))
    monkeypatch.setattr(ra, '_run_attempt',
                        lambda *a, **kw: (ra.EXIT_FAIL, [], '汇总'))
    assert ra.main(['--date', '2026-09-30', '--attempts', '1']) == ra.EXIT_FAIL


def test_run_agent交人之后不再自动起第二轮(monkeypatch, capsys):
    """`--attempts 3` 是给人手动重跑用的，不是让 C 类自己演三遍：
    第 2 轮开头 `reset.kill_all()` 会把用户正开着的通达信杀掉，而定位失败不会因为
    杀了客户端而变好。上限 1 次，立刻出声。"""
    from app.tdx_client import run_agent as ra, win32
    from app.tdx_client.machine import StepRecord

    kills: list = []
    calls: list = []
    monkeypatch.setattr(win32, 'session_ok', lambda: (True, '交互会话'))
    monkeypatch.setattr(win32, 'input_desktop_ok', lambda: (True, '可用'))
    monkeypatch.setattr(ra.creds, 'has_credentials', lambda *a, **kw: True)
    monkeypatch.setattr(ra.readiness, 'expected_trade_date',
                        lambda *a, **kw: dt.date(2026, 9, 30))
    monkeypatch.setattr(ra.reset, 'kill_all',
                        lambda *a, **kw: (kills.append(1) or (True, '清了')))

    def fake_attempt(*a, **kw):
        calls.append(1)
        return (ra.EXIT_HANDOFF,
                [StepRecord(key='s5_dialog', title='打开下载对话框', state='handoff',
                            message='四级都没打开对话框', seconds=1.0,
                            detail={'stuck_dir': r'var\stuck\x'})],
                '汇总')

    monkeypatch.setattr(ra, '_run_attempt', fake_attempt)
    assert ra.main(['--date', '2026-09-30', '--attempts', '3']) == ra.EXIT_HANDOFF
    assert len(calls) == 1, '交人之后还起了第 2 轮'
    assert kills == [], '第 2 轮会把用户正开着的客户端杀掉'
    out = capsys.readouterr().out
    assert '不再自动重来' in out and 'var\\stuck\\x' in out


# ---------------------------------------------------------------- 判据 6：自限预算
#
# 为什么这一组必须有：p0 的三步超时相加 6600s/轮、`--attempts 2` 最坏 13200s，
# 而最紧的上级 `daily_close_sync.GUI_TIMEOUT_SEC` 只给 2100s。
# 不自限的**后果不是"失败"，是"没人知道发生了什么"**：上级到点 `kill_tree` 把我们收掉，
# 对话框停在哪个状态、vipdoc 有没有写一半、这一轮算不算数，全都没有出口码。

class _Clock:
    """假钟。"预算够不够下一轮"必须是**确定的数**，不能靠桩子跑得刚好快。"""

    def __init__(self, round_cost: float = 300.0):
        self.t = 1_000_000.0
        self.round_cost = round_cost
        self.sleeps: list = []

    def time(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(round(s, 1))
        self.t += s


def _no_gui(monkeypatch, *, rounds_ret=(False, False, 1, ''), frontier_after=None,
            round_cost: float = 300.0):
    """把 main() 会碰的界面/文件判据/钟全换成桩，只留编排逻辑。"""
    calls: dict = {'one_round': [], 'kill_all': []}
    clock = _Clock(round_cost)

    def fake_one_round(_target, **kw):
        calls['one_round'].append(kw)
        clock.t += clock.round_cost
        return rounds_ret

    monkeypatch.setattr(p0, 'time', clock)
    monkeypatch.setattr(p0, 'one_round', fake_one_round)
    monkeypatch.setattr(p0, 'verdicts',
                        lambda *a, **kw: (False, False, ['桩：日线没到']))
    monkeypatch.setattr(p0, 'frontier', lambda: dict(
        frontier_after or {'max_date': '2026-09-29', 'sampled': 3, 'with_date': 3,
                           'downloading': False, 'sync': ''}))
    monkeypatch.setattr(p0.reset, 'snapshot', lambda *a, **kw: [])
    monkeypatch.setattr(p0.reset, 'kill_all',
                        lambda *a, **kw: (calls['kill_all'].append(1) or (True, '清了')))
    return calls, clock


def test_默认预算必须小于最紧的那个上级超时():
    """这条不测逻辑，只防"有人把任一边的常数改了"：预算 ≥ 上级超时 = 自限等于没有。"""
    from ingest.jobs import daily_close_sync as dcs

    assert p0.BUDGET_SEC < dcs.GUI_TIMEOUT_SEC
    assert p0.MIN_ROUND_SEC + p0.MIN_PHASE_SEC <= p0.BUDGET_SEC


def test_预算来源是命令行大于环境变量大于默认(monkeypatch):
    assert p0.budget_sec(900) == 900
    monkeypatch.setenv('TDX_PRELOAD_BUDGET_SEC', '1234')
    assert p0.budget_sec() == 1234
    assert p0.budget_sec(900) == 900                # 命令行还是赢
    monkeypatch.setenv('TDX_PRELOAD_BUDGET_SEC', 'abc')
    assert p0.budget_sec() == p0.BUDGET_SEC          # 脏值回落，不"看起来能配"
    assert p0.budget_sec(0) == p0.BUDGET_SEC         # 非正数不采纳
    assert p0.budget_sec(-5) == p0.BUDGET_SEC


def test_预算不够就不开新一步(monkeypatch):
    seen: list = []
    monkeypatch.setattr(p0, 'run_phase',
                        lambda name, keys, **kw: seen.append((name, kw['timeout'])) or
                        (1, '桩'))
    monkeypatch.setattr(p0, 'verdicts', lambda *a, **kw: (False, False, ['桩']))
    stopped = p0.one_round(dt.date(2026, 9, 30), day_only=True, rnd=1, rounds=1,
                           deadline=time.time() + 30)[3]
    assert seen == [], '只剩 30 秒还起一步，等于把中间状态扔给上级的 kill_tree'
    assert '预算' in stopped and '30' in stopped


def test_单步超时被总预算收窄而不是照抄步上限(monkeypatch):
    def run_case(seconds: float) -> list:
        seen: list = []

        def fake_run_phase(name, keys, **kw):
            seen.append((name, kw['timeout']))
            return 1, '桩'

        monkeypatch.setattr(p0, 'run_phase', fake_run_phase)
        monkeypatch.setattr(p0, 'verdicts', lambda *a, **kw: (False, False, ['桩']))
        p0.one_round(dt.date(2026, 9, 30), day_only=True, rnd=1, rounds=1,
                     deadline=time.time() + seconds)
        return seen

    tight = run_case(300)
    wide = run_case(9000)
    # 预算紧：300s < prepare 的 1200s 上限 → 每一步都按剩余收窄（且不低于 MIN_PHASE_SEC）
    assert [n for n, _ in tight] == ['prepare', 'day']
    assert all(120 <= t <= 300 for _, t in tight), tight
    # 预算宽：用各步自己的上限，不受影响
    assert wide == [('prepare', 1200), ('day', 1200)]


def test_main预算不够不起第二轮也不杀客户端(monkeypatch, capsys):
    calls, _ck = _no_gui(monkeypatch)              # 一轮花 300s
    rc = p0.main(['--date', '2026-09-30', '--attempts', '2', '--budget-sec', '120'])
    out = capsys.readouterr().out
    assert rc == 1
    # 120s 连一轮都放不下 → 明说后抬到 600s；跑完第一轮只剩 300s → 第 2 轮不许起
    assert '连一轮都放不下' in out, '小到跑不了一轮的预算要明说，不能静悄悄换数'
    assert len(calls['one_round']) == 1, '剩余预算 < MIN_ROUND_SEC 还硬挤一轮'
    assert calls['kill_all'] == [], '不起第 2 轮就不该杀用户的客户端'
    assert '第 2 轮没起' in out and '交回链子' in out


def test_main子进程要人来时不自动起第二轮(monkeypatch, capsys):
    """C 类的上限是 1 次：再演一遍只是同样的失败，而第 2 轮开头会 kill_all。"""
    calls, _ck = _no_gui(monkeypatch, rounds_ret=(False, False, p0.EXIT_HANDOFF, ''))
    assert p0.main(['--date', '2026-09-30', '--attempts', '3']) == 2
    assert len(calls['one_round']) == 1
    assert calls['kill_all'] == []
    assert '要人来接手' in capsys.readouterr().out


def test_main两轮之间至少隔ROUND_SPACING_SEC(monkeypatch, capsys):
    """另一半硬要求：不许在几十秒内把自己重试完，否则链的下一档（+25 分钟）永远等不到。"""
    calls, ck = _no_gui(monkeypatch, round_cost=10.0)
    p0.main(['--date', '2026-09-30', '--attempts', '2'])
    assert len(calls['one_round']) == 2
    assert ck.sleeps[:1] == [p0.ROUND_SPACING_SEC], \
        '桩里一轮只花 10 秒，第 2 轮前必须补齐到 60s 的间隔'
    assert '先隔' in capsys.readouterr().out


def test_main预算用完但还在推进时报晚到不是失败(monkeypatch, capsys):
    """预算把 B 类截断了，也不能报成"死了"：报成死了下游就去重启客户端。"""
    _no_gui(monkeypatch, rounds_ret=(False, False, 1, '预算只剩 90 秒'),
            frontier_after={'max_date': '2026-09-29', 'sampled': 3, 'with_date': 3,
                            'downloading': True, 'sync': 'TPython.exe(1)'})
    assert p0.main(['--date', '2026-09-30', '--budget-sec', '700']) == 4
    out = capsys.readouterr().out
    assert '被自限预算收的' in out and '晚到' in out

