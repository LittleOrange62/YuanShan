# -*- coding: utf-8 -*-
"""每日复盘那一栏（`ingest/jobs/push/t09_daily_review.py`，18:18）的钉住用例。

用户 11-03 的话是「再加一个：每日复盘，指定脚本路径和脚本名称即可，具体脚本我会去改这个脚本」，
所以这一路交付的是**壳**：内容函数留空，但壳的四条纪律必须当场可验——
①没内容就一条都不发（不往群里发一句空复盘）；②正文先落库再发（于是 `--resend` 能一字不差补发）；
③默认关着 ⇒ 连任务都不注册（关着的语义是注销，不是留个每天空跑的壳）；④措辞在模板里，不在代码里拼。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.intraday import store, tasks
from ingest.jobs.push import t09_daily_review as R


def test_默认关着并且册上给得出这一路():
    assert tasks.DEFAULTS['daily_review_enabled'] is False
    assert tasks.DEFAULTS['daily_review_times'] == '18:18'
    assert tasks.SPEC['daily_review']['task_name'] == 'YuanShan-DailyReview'
    assert tasks.task_args('daily_review', tasks.DEFAULTS) == \
        '-X utf8 -m ingest.jobs.push.t09_daily_review --send'


def test_关掉的那一路是注销任务不是留个空跑的壳(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(tasks, 'run_powershell',
                        lambda script, **kw: seen.append(script) or (0, 'GONE', ''))
    row = tasks.register_one('daily_review', tasks.DEFAULTS, want_on=False)
    assert row['action'] == 'removed' and 'Unregister-ScheduledTask' in seen[0]


def test_开着时生成的任务定义落在1818():
    cfg = {**tasks.DEFAULTS, 'daily_review_enabled': True}
    assert tasks.want_boundaries('daily_review', cfg)[0].endswith('T18:18:00')
    assert '每天 18:18' in tasks.schedule_text('daily_review', cfg)


def test_没写内容时一条都不发(monkeypatch):
    """`collect()` 还是空骨架 ⇒ 不发、不落成"已发送"，只记一行 `daily_review_empty`。"""
    codes: list[str] = []
    sent: list[str] = []
    monkeypatch.setattr(R, '_log', lambda now, what, **kw: codes.append(what))
    monkeypatch.setattr(R, '_send', lambda now, text, keys, **kw: sent.append(text))
    monkeypatch.setattr(tasks, 'send_channels', lambda c=None: ['feishu-main'])
    rc = R.one_round(dry_run=False, send=True, force=True, channel=None, resend=False)
    assert rc == 1 and codes == ['daily_review_empty'] and sent == []


def test_有内容时先落正文再发_补发只读库(monkeypatch):
    """顺序必须是"落库 → 发送"：那样 `--resend` 才拿得到**同一条正文**，而不是重新算一遍。"""
    order: list[str] = []
    monkeypatch.setattr(R, 'collect', lambda now: {'title': '每日复盘', 'sections': ['甲段', ' 乙段 ', '']})
    monkeypatch.setattr(R, '_log',
                        lambda now, what, **kw: order.append((what, kw.get('body') is not None)))
    monkeypatch.setattr(R, '_send', lambda now, text, keys, **kw: order.append(('sent', text)))
    rc = R.one_round(dry_run=False, send=True, force=True, channel=None, resend=False)
    assert rc == 0
    assert order[0] == ('daily_review', True), '正文没先落库'
    assert order[1][0] == 'sent' and '甲段' in order[1][1] and '乙段' in order[1][1]
    assert '【每日复盘' in order[1][1] and '**' not in order[1][1]
    assert '不打分不预测不给建议' in order[1][1]        # 页脚由模板给，不在代码里拼句子


def test_补发没有正文时不重算冒充(monkeypatch):
    codes, sent = [], []
    monkeypatch.setattr(R, 'today_body', lambda day: None)
    monkeypatch.setattr(R, '_log', lambda now, what, **kw: codes.append(what))
    monkeypatch.setattr(R, '_send', lambda now, text, keys, **kw: sent.append(text))
    rc = R.one_round(dry_run=False, send=True, force=True, channel=None, resend=True)
    assert rc == 1 and codes == ['daily_review_no_body'] and sent == []


def test_模板键在册_缺了当场红():
    t = R.tpl()
    for key in ('daily_review_header', 'daily_review_footer'):
        assert key in t, f'app/intraday/templates.json 少了 {key}'


def test_pytest_进程里硬挡真写留痕表():
    with pytest.raises(RuntimeError, match='analytics 表'):
        store.write_broadcast(dt.datetime(2026, 11, 3, 18, 18), task='daily_review',
                              what='daily_review', body='x')
