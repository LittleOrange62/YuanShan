# -*- coding: utf-8 -*-
"""概念热度那一炉（`ingest/jobs/push/t04_heat_report.py`）的版式与"发得少、存得全"钉住用例。

用户 11-03 定的是两句看起来同类、其实要分开守的话：
· 「涨跌停概念发前 12 个即可」——**只压屏上那一段**，库里 `analytics.intraday_concept_count`
  仍按 `heat_top`（25）全量落：屏上少列几行随时能改回来，库里少落的行第二天补不回来；
· 「最后的来源说明不用发送」——那句仍然进留痕表的 `body`/`source_detail`，
  事后问"这个概念的涨幅是哪天的"要答得上，只是不占群里一行。
"""
from __future__ import annotations

from app.intraday import store
from ingest.jobs.push import t04_heat_report as H


def _hits(n: int) -> list[dict]:
    return [{'name': f'概念{i}', 'n': 10 - (i % 7), 'sector_code': f'BK{i:04d}'} for i in range(n)]


def test_屏上只列前十二个_取数与入库仍按全量(monkeypatch):
    zt = {f'6000{i:02d}.SH': {'code': f'6000{i:02d}'} for i in range(30)}
    seen: dict = {}

    monkeypatch.setattr(H.zt_watch, 'fetch_pools', lambda now: (zt, {}, {}))

    def fake_counts(codes, *, max_members, sector_top, min_hits):
        seen['sector_top'] = sector_top
        seen['min_hits'] = min_hits
        return _hits(25), 0

    monkeypatch.setattr(H.sources, 'sector_counts', fake_counts)
    monkeypatch.setattr(H.sources, 'sector_pcts', lambda names: {})
    box: dict = {}
    monkeypatch.setattr(store, 'upsert_concept_counts',
                        lambda rows: box.__setitem__('rows', rows) or len(rows))
    monkeypatch.setattr(store, 'write_broadcast',
                        lambda *a, **kw: box.setdefault('casts', []).append(kw))
    monkeypatch.setattr(H.tasks, 'send_channels', lambda ch=None: ['feishu-main'])

    def fake_send(keys, title, body):
        box['body'] = body
        return {'ok': True, 'detail': 'ok'}
    monkeypatch.setattr('duanxian.notify.send_to', fake_send)

    assert H.one_round(dry_run=False, send=True, channel=None, force=True) == 0
    body = box['body']
    listed = [ln for ln in body.split('\n') if ln.startswith(('涨停股概念', '跌停股概念'))]
    assert len(listed) == 2, body
    for ln in listed:
        assert ln.count('、') + 1 == 12, f'屏上这一行不是 12 个：{ln}'
    assert '概念12' not in body and '概念24' not in body      # 第 13~25 个不进正文
    assert seen['sector_top'] == 25, '取数被屏上的 12 带着走了：库里会跟着少落'
    assert len(box['rows']) == 50, '涨停 25 + 跌停 25 行都要落库'
    assert '—东财涨跌停池' not in body and not body.rstrip().endswith('）')


def test_来源不发但留痕里还在(monkeypatch):
    """`intraday_broadcast` 的 `counts`/`body` 是事后唯一能说清"这一条是哪来的"的地方。"""
    zt = {f'6000{i:02d}.SH': {'code': f'6000{i:02d}'} for i in range(6)}
    box: dict = {}
    monkeypatch.setattr(H.zt_watch, 'fetch_pools', lambda now: (zt, {}, {}))
    monkeypatch.setattr(H.sources, 'sector_counts',
                        lambda codes, **kw: (_hits(3), 0))
    monkeypatch.setattr(H.sources, 'sector_pcts', lambda names: {})
    monkeypatch.setattr(store, 'upsert_concept_counts', lambda rows: len(rows))
    monkeypatch.setattr(store, 'write_broadcast',
                        lambda *a, **kw: box.__setitem__('cast', kw))
    monkeypatch.setattr(H.tasks, 'send_channels', lambda ch=None: ['feishu-main'])
    monkeypatch.setattr('duanxian.notify.send_to',
                        lambda keys, title, body: {'ok': True, 'detail': 'ok'})
    assert H.one_round(dry_run=False, send=False, channel=None, force=True) == 0
    cast = box['cast']
    assert cast['counts']['concept_rows'] == 6 and cast['counts']['zt_n'] == 6   # 涨停 3 + 跌停 3
    assert '涨停股概念' in cast['body']            # 正文原样存着，事后要查得到
