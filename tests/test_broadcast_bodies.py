# -*- coding: utf-8 -*-
"""「推送任务 → 按日期回看推送正文」那一块的钉住用例（用户 2026-11-10）。

用户原话：「5、9、10、11 这些分析的推送任务，都是先入库再推送或者推送的同时入库，
并且在系统页面"任务推送"中，可以选择日期查看推送具体内容。默认都显示最近一天的数据。」

所以这里钉四件事，件件都是"会说假话"的方向：
① **折成一条**：同一路同一钟点有正文行与回执行两份，屏上只能出现一次，且正文取真发出去那份；
② **没回执 ≠ 已发送**：`sent` 只说库里有没有回执那一行，送达看 `delivered`（⑧失败写的是 `wx_warn`）；
③ **只列真往外发的那几路**：①⑥只落库，它们的 body 是池摘要，列进来同一份变化量会出现两次；
④ **默认最近一天**是"最近一个有正文的日子"，不是今天；读不出来要出声，不许兜成"那天没发过"。

壳那一侧「先落库再发」由各自family钉着（`test_noon_review.py::test_池子取不到那一路也是先落正文再发`、
`test_daily_review.py`、`test_tomorrow_outlook.py`、`test_strong_mode.py`），这里最后两条拿真库回验。
"""
from __future__ import annotations

import datetime as dt
import re

import pytest

from app.db import engine
from app.intraday import store
from app.intraday.tasks import PUSH_KEYS, SPEC

AT = dt.datetime(2026, 11, 9, 18, 18, 3)


def _row(task='daily_review', what='daily_review', at=AT, body='正文甲', **kw):
    r = {'task': task, 'what': what, 'at_ts': at, 'body': body,
         'ok': None, 'delivered': None, 'channel_key': None, 'err': None}
    r.update(kw)
    return r


# ---------------------------------------------------------------- ① 折法（纯函数）

def test_正文与回执折成一条且正文取真发出去那份():
    rows = [_row(body='落库那一份'), _row(what='sent', body='发出去那一份',
                                        ok=True, delivered=True, channel_key='feishu-main')]
    got = store.fold_bodies(rows)
    assert list(got) == ['daily_review'] and len(got['daily_review']) == 1
    one = got['daily_review'][0]
    assert one['body'] == '发出去那一份'
    assert one['kinds'] == ['daily_review', 'sent']
    assert one['sent'] is True and one['delivered'] is True
    assert one['channel_key'] == 'feishu-main'
    assert one['at'] == '2026-11-09 18:18:03' and one['chars'] == len('发出去那一份')


def test_没有回执那一行时不许读成已发送():
    one = store.fold_bodies([_row()])['daily_review'][0]
    assert one['sent'] is False
    assert one['ok'] is None and one['delivered'] is None
    assert one['body'] == '正文甲', '没有回执也要给得出正文（那正是"先入库再推送"的价值）'


def test_微信镜像那种失败留痕要把原话带出来():
    """⑧ 失败时写的是 `wx_warn` 而不是 `sent`：`delivered=False` 且有原话。
    读侧不许把它洗成"只是没回执"——那会把一次真失败说成观测层缺行。"""
    one = store.fold_bodies([_row(task='mirror', what='wx_warn', ok=False, delivered=False,
                                  channel_key='wx', err='没找到微信主窗口')])['mirror'][0]
    assert one['sent'] is False and one['delivered'] is False
    assert one['err'] == '没找到微信主窗口' and one['kinds'] == ['wx_warn']


def test_没有正文的行不进列表():
    assert store.fold_bodies([_row(body=None), _row(body=''), _row(what='digest', body=None)]) == {}


def test_同一路多针按钟点升序且各算一条():
    early = AT - dt.timedelta(hours=1)
    got = store.fold_bodies([_row(at=AT, body='晚'), _row(at=early, body='早')])['daily_review']
    assert [r['body'] for r in got] == ['早', '晚']


def test_不同路同一钟点不许并成一条():
    got = store.fold_bodies([_row(task='noon', what='noon'), _row(task='tomorrow', what='tomorrow')])
    assert set(got) == {'noon', 'tomorrow'}
    assert all(len(v) == 1 for v in got.values())


# ---------------------------------------------------------------- ③ 只列真往外发的那几路

def test_PUSH_KEYS_剔掉只落库那两路且顺序同在册表():
    assert 'watcher' not in PUSH_KEYS and 'sysentry_fill' not in PUSH_KEYS
    assert PUSH_KEYS == tuple(k for k in SPEC if k not in ('watcher', 'sysentry_fill'))
    assert set(PUSH_KEYS) == {k for k, s in SPEC.items() if s.get('mode', 'push') != 'record'}


@pytest.mark.parametrize('fn,sql_bits', [
    (lambda: store.broadcast_bodies(dt.date(2026, 11, 9)), ('body IS NOT NULL', 'trade_date = %s')),
    (lambda: store.broadcast_body_days(limit=7), ('body IS NOT NULL', 'DISTINCT trade_date')),
])
def test_两个读法都只问真往外发的那几路且只读(fn, sql_bits, monkeypatch):
    seen = []

    def fake_query(sql, params=None, **kw):
        seen.append((sql, params, kw))
        return []
    monkeypatch.setattr(engine, 'query', fake_query)
    fn()
    assert len(seen) == 1
    sql, params, kw = seen[0]
    for bit in sql_bits:
        assert bit in sql, f'SQL 里少了「{bit}」：没有正文的行混进来就会在屏上变成一条空白推送'
    assert 'task = ANY(%s::text[])' in sql
    # 两个读法的参数序不同：正文那支是 (日子, 版本, 路名表)，日子那支是 (版本, 路名表, 上限)
    at = 1 if 'DISTINCT' in sql else 2
    assert list(params)[at] == list(PUSH_KEYS)
    assert kw.get('role') == 'reader', '回看是只读面，不许拿写角色'
    if 'DISTINCT' in sql:
        assert 'ORDER BY trade_date DESC' in sql and 'LIMIT' in sql


def test_有哪些日子是降序且带上限(monkeypatch):
    seen = []
    monkeypatch.setattr(engine, 'query',
                        lambda sql, params=None, **kw: seen.append((sql, params)) or [
                            {'trade_date': dt.date(2026, 11, 9)}, {'trade_date': dt.date(2026, 11, 6)}])
    assert store.broadcast_body_days(limit=7) == ['2026-11-09', '2026-11-06']
    assert 'ORDER BY trade_date DESC' in seen[0][0] and seen[0][1][-1] == 7


def test_读不出来要抛不许兜成从来没发过(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError('库挂了')
    monkeypatch.setattr(engine, 'query', boom)
    with pytest.raises(RuntimeError):
        store.broadcast_body_days()
    with pytest.raises(RuntimeError):
        store.broadcast_bodies(dt.date(2026, 11, 9))


# ---------------------------------------------------------------- ④ 端点那一层

@pytest.fixture()
def client():
    """走 `server.app`（不要 `sys.path.insert(0,"vr") + import app`：模块名会被抢走）。"""
    from fastapi.testclient import TestClient
    import server
    return TestClient(server.app, base_url="http://127.0.0.1:8910", client=("127.0.0.1", 1))


@pytest.fixture()
def fake_reads(monkeypatch):
    calls = []
    monkeypatch.setattr(store, 'broadcast_body_days',
                        lambda **kw: ['2026-11-09', '2026-11-06', '2026-11-05'])
    monkeypatch.setattr(store, 'broadcast_bodies',
                        lambda day: calls.append(day) or [
                            {'task': 'noon', 'rounds': [store.fold_bodies([_row(task='noon', what='noon')])['noon'][0]]}])
    return calls


def test_缺省落在最近一个有正文的日子而不是今天(client, fake_reads):
    got = client.get('/api/intraday/broadcast/bodies')
    assert got.status_code == 200, got.text
    data = got.json()['data']
    assert data['day'] == '2026-11-09' and data['latest'] == '2026-11-09'
    assert fake_reads == [dt.date(2026, 11, 9)], '缺省读的是最近那一天，不是今天'
    assert data['readable'] is True and data['items'][0]['task'] == 'noon'
    assert data['days'] == ['2026-11-09', '2026-11-06', '2026-11-05']


def test_指定哪一天就读哪一天(client, fake_reads):
    data = client.get('/api/intraday/broadcast/bodies?day=2026-11-06').json()['data']
    assert fake_reads == [dt.date(2026, 11, 6)]
    assert data['day'] == '2026-11-06' and data['latest'] == '2026-11-09'


def test_日期不成形状回400并把原话说清楚(client, fake_reads):
    for bad in ('2026-13-45', '11/09', 'yesterday'):
        r = client.get('/api/intraday/broadcast/bodies', params={'day': bad})
        assert r.status_code == 400, r.text
        assert 'YYYY-MM-DD' in r.json()['detail']
    assert fake_reads == [], '日子没验过就不该打库'


def test_库读不出来时端点出声而不是说那天没发过(client, monkeypatch):
    def boom(**kw):
        raise RuntimeError('库挂了')
    monkeypatch.setattr(store, 'broadcast_body_days', boom)
    data = client.get('/api/intraday/broadcast/bodies').json()['data']
    assert data['readable'] is False and '库挂了' in data['reason']
    assert data['items'] == [] and data['latest'] is None


def test_那天真没有正文时是空列表但仍可读(client, monkeypatch):
    monkeypatch.setattr(store, 'broadcast_body_days', lambda **kw: ['2026-11-09'])
    monkeypatch.setattr(store, 'broadcast_bodies', lambda day: [])
    data = client.get('/api/intraday/broadcast/bodies?day=2026-11-08').json()['data']
    assert data['readable'] is True and data['items'] == [] and data['day'] == '2026-11-08'


# ---------------------------------------------------------------- 真库回验（不打桩）

def test_真库最近一天每一路每一条都回看得出来():
    days = store.broadcast_body_days()
    assert days, '真库里这些天应有正文（②③④⑤⑦⑧⑨⑩ 都跑过）'
    assert days == sorted(days, reverse=True)
    items = store.broadcast_bodies(dt.date.fromisoformat(days[0]))
    assert items, f'{days[0]} 应读得出正文'
    assert [it['task'] for it in items] == [k for k in PUSH_KEYS if k in {i['task'] for i in items}]
    for it in items:
        assert it['task'] not in ('watcher', 'sysentry_fill')
        for r in it['rounds']:
            assert r['body'] and r['chars'] == len(r['body'])
            assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', r['at']), r['at']
            assert r['kinds'] and isinstance(r['sent'], bool)


@pytest.mark.parametrize('task,what', [('noon', 'noon'), ('daily_review', 'daily_review'),
                                       ('tomorrow', 'tomorrow')])
def test_真库里这三路的正文与回执同一钟点且逐字相同(task, what):
    """用户要的「先入库再推送」在真数据上的形状：同一路同一 `at_ts` 既有正文行也有回执行，
    两份正文逐字相同 —— 不同就说明发送前落的与真发出去的不是同一串（重跑会改口）。
    ⑪ 最强模式 11-10 才上线，库里还没有它的行，那一路的顺序由 `test_strong_mode.py` 钉。
    """
    row = engine.query_one(
        'SELECT max(trade_date) AS d FROM analytics.intraday_broadcast'
        ' WHERE task = %s AND what = %s AND body IS NOT NULL', (task, what), role='reader')
    assert row and row['d'], f'真库里应有 {task} 的正文行'
    items = {it['task']: it for it in store.broadcast_bodies(row['d'])}
    assert task in items and items[task]['rounds'], f'{task} 在 {row["d"]} 回看不了'
    # 两份正文是否逐字相同，交给一次 SQL 自连接：`r['at']` 为屏上好看截到秒，
    # 拿它回填去等值匹配 `at_ts`（库里带微秒）会匹配不到，那是测试自己的假红。
    pair = engine.query(
        'SELECT a.at_ts, a.what AS pre_what, a.body AS pre, b.body AS sent'
        '  FROM analytics.intraday_broadcast a'
        '  JOIN analytics.intraday_broadcast b'
        '    ON b.trade_date = a.trade_date AND b.task = a.task'
        '   AND b.at_ts = a.at_ts AND b.what = %s AND b.spec_version = a.spec_version'
        ' WHERE a.trade_date = %s AND a.task = %s AND a.what <> %s'
        '   AND a.body IS NOT NULL AND a.spec_version = %s',
        ('sent', row['d'], task, 'sent', store.SPEC_VERSION), role='reader')
    assert pair, (f'{task} 在 {row["d"]} 找不到「发送前正文行 + 同钟点回执行」这一对 —— '
                  '「先入库再推送」在这天不成立')
    for p in pair:
        assert p['pre'] == p['sent'], f'{task} @{p["at_ts"]} 落库那一份与真发出去那一份不一样'