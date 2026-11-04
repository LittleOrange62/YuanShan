# -*- coding: utf-8 -*-
"""盘中变化量六点推送（`ingest/jobs/push/t03_zt_delta.py`）与"取数/推送分层"的钉住用例。

用户 11-03 的原话是「判断与取数属于数据加工环节……跟推送到飞书的行为分开，不要互相影响」。
落到代码上就是三条：①`zt_watch` 只落库（任务参数里没有 `--send`）；
②`zt_delta` 一次网络请求都不发（全部输入是已落库的行）；
③概念热度的时点由 `push_times` 推导（+10 秒），不再有第二份可改的时点。
这三条各钉一条，谁改回去当场红。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.intraday import tasks
from ingest.jobs.push import t03_zt_delta as D


# ---------------------------------------------------------------- 分层：谁取数、谁只读库

def test_事件差那一炉的任务参数里没有_send():
    """采集与推送分家的直接证据：老写法是 `--once --send`（取一针顺手播一次）。"""
    cfg, _ = tasks.load_cfg()
    assert tasks.task_args('watcher', cfg) == '-X utf8 -m ingest.jobs.push.t01_zt_watch --once'
    assert '--send' not in tasks.task_args('watcher', cfg)


def test_变化量这一炉不碰源站():
    """它只许读 PG：一旦有人图省事在这里加一次 HTTP，"事后可重跑、重跑内容一致"就废了，
    而且重跑那一次源站翻页会给出一条与当时不同的消息 —— 那种差别没人能察觉。"""
    import inspect
    import urllib.request as U

    src = inspect.getsource(D)
    for forbidden in ('urlopen', 'requests.', 'fetch_pools', 'sector_counts'):
        assert forbidden not in src, f'zt_delta 里出现了 {forbidden}：这一炉只许读库'
    with pytest.raises(AttributeError):
        D.urllib        # 模块级就没有 urllib 可用


# ---------------------------------------------------------------- 窗口与正文

def test_窗口起点接上次推送():
    from app.db import engine
    orig = engine.scalar
    seen: list[str] = []

    def fake(sql, params=None, role=None):
        seen.append(sql)
        return dt.datetime(2026, 11, 3, 10, 15, 5, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    engine.scalar = fake
    try:
        now = dt.datetime(2026, 11, 3, 11, 0, 2, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        lo = D.window_start(now, now.date(), None)
        assert f'{lo:%H:%M:%S}' == '10:15:05'
    finally:
        engine.scalar = orig
    assert "task = %s" in seen[0] and "what = 'sent'" in seen[0]


def test_手工指定窗口起点只影响本次():
    now = dt.datetime(2026, 11, 3, 14, 0, 1)
    lo = D.window_start(now, now.date(), '11:00')
    assert f'{lo:%H:%M}' == '11:00'


def test_同一只票一窗口内反复出现只留最后一次():
    """09:36 新封、09:41 炸板、09:44 回封 —— 变化量要说"现在什么样"，不能把同一只数三遍。"""
    from app.db import engine
    orig = engine.query
    tz = dt.timezone(dt.timedelta(hours=8))
    rows = [
        {'kind': 'seal_new', 'kind_label': '新封', 'security_code': '600001', 'security_name': '甲',
         'pct': 9.9, 'board_count': 1, 'open_count': 0, 'first_seal_time': '09:36',
         'pool_zt_n': 58, 'pool_zb_n': 7, 'pool_dt_n': 3, 'captured_at': dt.datetime(2026, 11, 3, 9, 36, tzinfo=tz)},
        {'kind': 'unseal', 'kind_label': '炸板', 'security_code': '600001', 'security_name': '甲',
         'pct': 6.1, 'board_count': 1, 'open_count': 1, 'first_seal_time': '09:36',
         'pool_zt_n': 57, 'pool_zb_n': 8, 'pool_dt_n': 3, 'captured_at': dt.datetime(2026, 11, 3, 9, 41, tzinfo=tz)},
    ]
    engine.query = lambda sql, params=None, role=None: rows
    try:
        groups, summary = D.window_events(
            dt.date(2026, 11, 3),
            dt.datetime(2026, 11, 3, 9, 24, tzinfo=tz),
            dt.datetime(2026, 11, 3, 9, 45, tzinfo=tz))
    finally:
        engine.query = orig
    assert len(groups['seal_new']) == 1 and len(groups['unseal']) == 1
    assert summary['n_rows'] == 2 and summary['n_distinct'] == 2
    assert summary['pool'] == {'zt': 57, 'zb': 8, 'dt': 3}      # 取最后一针的家数


def test_无变化与没落库是两句话(monkeypatch):
    """库里今天零行 ⇒ 不发"无变化"（那是把"没跑"说成"跑过了没动静"）。"""
    codes = []
    monkeypatch.setattr(D, 'window_start', lambda now, day, o: now.replace(hour=9, minute=24))
    monkeypatch.setattr(D, 'window_events',
                        lambda day, lo, hi: ({}, {'n_rows': 0, 'n_distinct': 0,
                                                  'pool': None, 'pool_asof': None}))
    monkeypatch.setattr(D, 'latest_pool', lambda day, hi: (None, None))
    monkeypatch.setattr(D, '_log', lambda now, what, **kw: codes.append(what))
    rc = D.one_round(dry_run=False, send=True, force=True, since=None, channel='feishu-main')
    assert rc == 1 and codes == ['delta_no_data']


def test_空窗口退到最近一针的家数_真库不打桩():
    """`latest_pool` 是"这一窗没事件时"的兜底路径，恰恰是明天最容易走到的那条
    （09:45 那一发完全可能零事件）。这条不桩 `engine`：SQL 写错（聚合列漏 GROUP BY 之类）
    在桩里永远看不出来，只有真库会红。"""
    from app.db import engine
    day = engine.scalar("select max(trade_date) from analytics.intraday_pool_event", role='reader')
    assert day, '库里没有事件行，这条没法验'
    hi = engine.scalar(
        "select max(captured_at) from analytics.intraday_pool_event where trade_date = %s",
        (day,), role='reader')
    pool, asof = D.latest_pool(day, hi)
    assert pool and set(pool) == {'zt', 'zb', 'dt'} and asof == hi
    # 窗口起点压在最后一针上 ⇒ 零事件，走的正是这条兜底
    groups, summary = D.window_events(day, hi, hi)
    assert summary['n_rows'] == 0 and not summary['pool']
    text = D.render(groups, summary, hi, hi, pool, asof)
    assert '本窗口无新增事件' in text and '家数见下一行' in text
    assert text.split('\n')[-1].startswith('涨停池')


def test_正文含各类只数与家数时点():
    now = dt.datetime(2026, 11, 3, 9, 45, 3)
    lo = dt.datetime(2026, 11, 3, 9, 35, 0)
    groups = {'seal_new': [{'code': '600001', 'name': '甲', 'board_count': 2, 'pct': 10.0,
                            'open_count': 0, 'first_seal_time': '09:41'},
                          {'code': '600002', 'name': '乙', 'board_count': 1, 'pct': 9.9,
                           'open_count': 0, 'first_seal_time': None}],
              'unseal': [{'code': '600003', 'name': '丙', 'board_count': 3, 'pct': 5.0,
                          'open_count': 2, 'first_seal_time': '09:38'}]}
    text = D.render(groups, {'n_rows': 3}, now, lo, {'zt': 61, 'zb': 9, 'dt': 4}, lo)
    lines = text.split('\n')
    # 用户 11-03 定的版式：标题不重复发（推送本身带标题），第一行就是名字、第二行直接"自……"
    assert lines[0] == '盘中涨跌停变化量' and lines[1] == '自 09:35 起'
    assert '—读 analytics' not in text, '最后那句来源说明已经按用户要求撤掉（它仍在留痕表里）'
    assert '【' not in text
    assert '新封 2只：甲2板、乙1板' in text
    # 板数高的排前面（与 zt_watch 同一套排序口径）
    assert '新封 2只：甲2板、乙1板' in text
    assert '炸板 1只：丙3板' in text and '涨停池61家' in text
    assert '截至 09:35:00 那一针' in text          # 家数是上一针的，就必须标出来
    fresh = D.render(groups, {'n_rows': 3}, now, lo, {'zt': 61, 'zb': 9, 'dt': 4}, now)
    assert '截至' not in fresh                     # 家数就是这一针的，不多加一句废话


# ---------------------------------------------------------------- 六点共用一格

def test_热度时点由push_times推导且错开十秒():
    cfg = {**tasks.DEFAULTS, 'push_times': '09:35,11:00,14:00'}
    assert tasks.derived_times('push_times', cfg) == ['09:35', '11:00', '14:00']
    assert tasks.derived_times('push_times+10s', cfg) == \
        ['09:35:10', '11:00:10', '14:00:10']
    assert '跟着「盘中涨跌停变化量」那一格' in tasks.schedule_text('heat', cfg)
    # delta 自己就是那一格：句子不许反过来指自己（屏上"跟着自己那一格"是最容易读成 bug 的一句）
    assert tasks.schedule_text('delta', cfg) == '每天 09:35、11:00、14:00'


def test_推导那一行不再有自己的时点键():
    cfg, _ = tasks.load_cfg()
    assert 'heat_times' not in cfg
    why = tasks.validate_patch({'heat_times': '10:05'}, cfg)
    assert why and 'push_times' in why


def test_push_times写坏整条不写():
    cfg, _ = tasks.load_cfg()
    assert tasks.validate_patch({'push_times': '09:35,25:99'}, cfg)
    assert tasks.validate_patch({'push_times': '09:35,11:00'}, cfg) is None


def test_delta行生成六个触发点():
    cfg, _ = tasks.load_cfg()
    bounds = tasks.want_boundaries('delta', cfg)
    assert len(bounds) == len(tasks.parse_times(cfg['push_times']))
    assert bounds[0].endswith('T09:35:00')
