# -*- coding: utf-8 -*-
"""竞价榜微信镜像（`ingest/jobs/push/t08_wx_mirror.py`）的钉住用例。

外部那条 `Xiaodi-WxMirrorAuction` 到 11-03 已经 10 次 `mirror_timeout`、0 次送达 ——
它等的是观澜那份 `var/zt_events.jsonl`，而 10-26 起竞价榜的留痕改进本项目的
`analytics.intraday_broadcast`，那份文件不再长新行。**没人报错**，因为失败落在它自己
var 里的一行日志上。收编后的这一条把同一件事记进留痕表，界面上"缺数/降级"那一格会显示，
所以这里的用例重点不是"能发"，而是**没发成必须留得下证据**。
"""
from __future__ import annotations

import datetime as dt

import pytest

from ingest.jobs.push import t08_wx_mirror as M


# ---------------------------------------------------------------- 正文来源与分块

def test_只认今天真发出去的那份正文():
    """`what='sent' AND ok` 之外的行（digest/未就绪/失败）都不许当镜像源 ——
    否则微信里会出现一条飞书根本没发过的东西。"""
    sql = None
    orig = M.today_body
    import app.db.engine as E

    def fake_query(sql_text, params, role=None):
        nonlocal sql
        sql = sql_text
        return {'body': '【竞价速览 09:25】…'}
    monkey_query = E.query_one
    E.query_one = fake_query
    try:
        assert M.today_body(dt.date(2026, 11, 3)) == '【竞价速览 09:25】…'
    finally:
        E.query_one = monkey_query
    assert "what = 'sent'" in sql and 'ok IS TRUE' in sql and 'task = %s' in sql


def test_超长按行切块并标序号():
    body = '\n'.join(f'第{i}行 ' + 'x' * 40 for i in range(40))
    parts = M.chunk(body, 120)
    assert len(parts) > 1
    assert all(len(p) <= 120 for p in parts), [len(p) for p in parts]
    assert parts[0].endswith('(1/%d)' % len(parts)) and parts[-1].endswith('(%d/%d)' % (len(parts), len(parts)))
    # 不超限时**不许**加序号：屏上多出来的 "(1/1)" 会让人以为被切过
    assert M.chunk('短的一段', 1200) == ['短的一段']


def test_空白正文也要回一块不返回空列表():
    assert M.chunk('', 1200) == ['']


# ---------------------------------------------------------------- 等待与去重

def test_等到上限还没正文就报超时():
    clock = [dt.datetime(2026, 11, 3, 9, 26)]
    slept = []
    calls = {'n': 0}
    orig = M.today_body
    M.today_body = lambda day: (calls.__setitem__('n', calls['n'] + 1) or None)
    try:
        got = M.wait_for_body(dt.date(2026, 11, 3), until='09:30', interval_s=20,
                             now_fn=lambda: clock[0], sleep_fn=lambda s: (slept.append(s),
                                                                          clock.__setitem__(0, clock[0] + dt.timedelta(seconds=s))))
        assert got is None and clock[0] >= dt.datetime(2026, 11, 3, 9, 30)
        assert calls['n'] >= 2 and slept          # 真的回头看过，不是一次性判定
    finally:
        M.today_body = orig


def test_同一天同一份正文不镜像第二遍(tmp_path, monkeypatch):
    monkeypatch.setattr(M, 'STATE_FILE', tmp_path / 'state.json')
    assert not M.already_mirrored(dt.date(2026, 11, 3), 'abc')
    M.mark_mirrored(dt.date(2026, 11, 3), 'abc', 3)
    assert M.already_mirrored(dt.date(2026, 11, 3), 'abc')
    assert not M.already_mirrored(dt.date(2026, 11, 4), 'abc')      # 换日重来
    assert not M.already_mirrored(dt.date(2026, 11, 3), 'abc2')     # 正文变了（改了模板要能重镜像）


# ---------------------------------------------------------------- 留痕（这条用例存在的理由）

def test_没等到正文也要在留痕表里留一行(monkeypatch, tmp_path):
    casts = []
    monkeypatch.setattr(M, 'today_body', lambda day: None)
    monkeypatch.setattr(M, '_log', lambda now, what, **kw: casts.append((what, kw)))
    monkeypatch.setattr(M, 'zt_trade_day', lambda now: True)
    monkeypatch.setattr(M, 'wait_for_body', lambda day, **kw: None)
    rc = M.one_round(dry_run=False, force=True, until='09:35', interval_s=1)
    assert rc == 3
    assert ('mirror_timeout', {'err': '等到 09:35'}) in [(w, k) for w, k in casts] or \
        any(w == 'mirror_timeout' for w, _ in casts), casts


def test_预检挡回来时记的是失败不是成功(monkeypatch):
    """桌面没解锁（RDP 一断就这样）必须留 `wx_warn` + 原因，且**不写去重标记** ——
    写了标记就等于今天再也不会补镜像。"""
    casts, marked, sends = [], [], []
    monkeypatch.setattr(M, 'today_body', lambda day: 'a\nb')
    monkeypatch.setattr(M, '_log', lambda now, what, **kw: casts.append((what, kw)))
    monkeypatch.setattr(M, 'already_mirrored', lambda d, h: False)
    monkeypatch.setattr(M, 'mark_mirrored', lambda d, h, p: marked.append('marked'))
    import duanxian.wxpush as WX
    monkeypatch.setattr(WX, 'send', lambda **kw: (sends.append(kw) or
                                                 {'ok': False, 'status': 'blocked', 'reason': '没有输入桌面'}))
    rc = M.one_round(dry_run=False, force=True, until='09:35', interval_s=1)
    assert rc == 2 and marked == [] and len(sends) == 1        # 挡回来就停手，不写标记
    assert [w for w, _ in casts] == ['mirror', 'wx_warn']
    assert '没有输入桌面' in casts[-1][1]['err']


def test_全部块发出去才算送达并落去重标记(monkeypatch):
    casts, marked = [], []
    monkeypatch.setattr(M, 'today_body', lambda day: 'a\nb')
    monkeypatch.setattr(M, '_log', lambda now, what, **kw: casts.append((what, kw)))
    monkeypatch.setattr(M, 'already_mirrored', lambda d, h: False)
    monkeypatch.setattr(M, 'mark_mirrored', lambda d, h, p: marked.append((d, p)))
    import duanxian.wxpush as WX
    monkeypatch.setattr(WX, 'send', lambda **kw: {'ok': True, 'status': 'injected', 'to': '2026double'})
    monkeypatch.setattr(WX, 'cooldown_seconds', lambda: 0)
    assert M.one_round(dry_run=False, force=True, until='09:35', interval_s=1) == 0
    assert [w for w, _ in casts][-1] == 'sent' and casts[-1][1]['ok'] is True
    assert marked and marked[0][1] == 1          # 一块也算发全，标记里记块数


def test_dry_run_一个键都不按也不写标记(monkeypatch):
    casts, marked, calls = [], [], []
    monkeypatch.setattr(M, 'today_body', lambda day: 'a\nb')
    monkeypatch.setattr(M, '_log', lambda now, what, **kw: casts.append(what))
    monkeypatch.setattr(M, 'already_mirrored', lambda d, h: False)
    monkeypatch.setattr(M, 'mark_mirrored', lambda *a: marked.append(1))
    import duanxian.wxpush as WX
    monkeypatch.setattr(WX, 'send', lambda **kw: (calls.append(kw.get('dry_run')) or
                                                 {'ok': True, 'status': 'dry_run'}))
    assert M.one_round(dry_run=True, force=True, until='09:35', interval_s=1) == 0
    assert calls == [True] and marked == [] and casts == []   # dry-run 连「跑过一针」都不记
