# -*- coding: utf-8 -*-
"""`c10_catchup`（加工链的"日线还在不在 vipdoc"那一步）的两条回归。

这两条都是 2026-09-29 实跑 e1 时才暴露的，而且都是**安静**的那种坏法：

1. 它调盘后下载时写的是参考仓库的路径 `scripts/ops/p0_tdx_predownload.py`，
   本项目那个作业在 `ingest/jobs/` → 每次 `can't open file`，退出码 2，
   输出看着像"通达信下载没把 vipdoc 推到目标日"，其实**一条命令都没执行**
   （和当年 `e1_fill_all` 的 15 步指向不存在目录是同一个形状）。
2. 库里明明已经有目标日（当天是 TQ 那条腿写的 5,559 行），它却因为
   "新股没有 pre_close"这几天被并进**待取数**清单，判 vipdoc 判不到 → 去下载 →
   退出码 2 → 于是 e1 把"日线没追上"当真，**跳过基础因子、评分、板块热度趋势**。
   用户看到的"趋势评分没算""四维象限没算好"就是这么来的。
   `dates` 管"要不要去取数"，`touched` 管"要回算哪几天"，两者不许再合成一条清单。
"""
from __future__ import annotations

import datetime as dt
import pathlib
import subprocess

import pytest

from ingest.jobs import c10_catchup as c10

TARGET = dt.date(2026, 9, 29)
SOURCE = (pathlib.Path(c10.__file__).read_text(encoding='utf-8'))


class _Verdict:
    ok = False
    note = '合成判据：vipdoc 没到目标日'

    def report(self):
        return '[个股日线] %s' % self.note


def test_前置下载走模块名不写脚本路径(monkeypatch):
    """行为级锁：真把 argv 抓下来看，不是 grep 源码。

    2026-10-06 随 `c10` 一起改：起子作业从 `subprocess.run(timeout=)` 换成
    `Popen + wait(timeout) + proc.kill_tree`。原因不是风格 —— `subprocess.run` 到点
    只杀它自己起的那个 p0，p0 下面挂的 `run_agent` 会变孤儿继续跑（"链说超时了，
    通达信还在动"）。断言原样保留：入口是 `-m`、没有 `.py` 路径、超时值交得出、
    **退出码 0 不算成功（判据只看文件）**。
    """
    seen = {}

    class _FakeP:
        pid = 4321

        def wait(self, timeout=None):
            seen['wait_timeout'] = timeout
            return 0

    def fake_popen(cmd, **kw):
        seen['cmd'] = list(cmd)
        seen['cwd'] = kw.get('cwd')
        return _FakeP()

    monkeypatch.setattr(subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(c10.proc, 'kill_tree',
                        lambda pid: seen.setdefault('killed', pid) or '不该被杀')
    monkeypatch.setattr(c10, 'vipdoc_verdict', lambda _d: _Verdict())
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)   # 假 Popen 已就位，才允许过闸
    ok = c10.try_tdx_download(TARGET, timeout=60)
    assert ok is False                       # 判据只看文件，退出码 0 不算成
    argv = seen['cmd']
    assert argv[:3] == [argv[0], '-u', '-m'], '作业入口必须是 -m 模块名：%s' % argv
    assert 'ingest.jobs.p0_tdx_predownload' in argv
    assert '--day-only' in argv and '--date' in argv and TARGET.isoformat() in argv
    assert not any('scripts' in a or a.endswith('.py') for a in argv[1:]), \
        '拼参考仓库路径会让每一步都 can\'t open file：%s' % argv
    assert seen['wait_timeout'] == 60        # 上限仍然交得出（不是"能跑多久算多久"）
    assert 'killed' not in seen              # 正常退出没收进程树


def test_超时是按进程树收的不是杀一层(monkeypatch):
    """`subprocess.run(timeout=)` 那种"只杀直接子进程"的写法必须被拦下来。"""
    calls = []

    class _FakeP:
        pid = 9911
        n = 0

        def wait(self, timeout=None):
            # 第一次是"等超时"，第二次是收完树之后回收那个已死的子进程
            type(self).n += 1
            if type(self).n == 1:
                raise subprocess.TimeoutExpired(['x'], 60)
            return -9

    monkeypatch.setattr(subprocess, 'Popen', lambda cmd, **kw: _FakeP())
    monkeypatch.setattr(c10.proc, 'kill_tree', lambda pid: calls.append(pid) or '收了')
    monkeypatch.setattr(c10, 'vipdoc_verdict', lambda _d: _Verdict())
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)   # 这一测就是要走真起的那段，用假 Popen 顶住
    assert c10.try_tdx_download(TARGET, timeout=60) is False
    assert calls == [9911], '超时要按 pid 收整棵进程树'


def test_派生列空着的日子不再被当成待取数():
    """源码级锁：这一条藏在 `main()` 的一段线性流程里（argparse + 六次查询），
    没有函数可以单测；与其为测试重构一段一次性的编排，不如把"不许再并进 dates"
    这句话直接钉住 —— 它挡的是一个每天演一次的假故障。"""
    # 注意别钉成 `'dates.append' not in SOURCE`：`sync_calendar` 里往日历清单 append
    # 是完全正常的（第一版就是这么写的，于是它一直红、红得毫无意义）。
    # 要钉的是"把 `nulls` 那几天并进**待取数**清单"这一句。
    assert "dates.append(r['trade_date'])" not in SOURCE, \
        'pre_close 为 NULL 的日子不能再并进"待取数"清单（见文件头第 2 条）'
    # 只回算那条路必须存在，并且把这几天交给 finish()
    assert '只回算派生列' in SOURCE
    assert "return finish(sorted({r['trade_date'] for r in nulls})" in SOURCE


def test_回算清单包含这几天():
    """`touched` 是"要回算哪几天"的清单 —— nulls 要进它，否则回算会被漏掉。"""
    assert "set(dates) | {r['trade_date'] for r in nulls}" in SOURCE


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))
