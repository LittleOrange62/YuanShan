# -*- coding: utf-8 -*-
"""c16 被中断时不许把半截当成功，以及"已齐整日的文件"要真的跳过。

两条都是 2026-09-26 实测出来的坑：
* batch 311 是一次 `--market bj --period 1` 的**成功**实跑（5 秒、83,523 行），
  库里留下"09-23 只有北交所 351 只"的半截日子，而 max(dt) 看着像到了；
* 中断（timeout 抢杀 / Ctrl-C / SIGTERM）走的是 `BaseException`，
  原来那句 `except Exception` 接不住 → 批次永远停在 `running`，页面上看不见失败。

全部打桩，不碰 vipdoc、不连库写。
"""
from __future__ import annotations

import datetime as dt

from ingest.jobs import c16_minute as c16


def test_中断把批次记成_failed_并返回_130(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(c16.ops_repo, 'begin_batch', lambda *a, **k: 777)
    monkeypatch.setattr(c16.ops_repo, 'end_batch',
                        lambda bid, status='ok', row_count=None, message='': calls.append(
                            (bid, status, message)))
    monkeypatch.setattr(c16, 'create_staging', lambda: None)
    monkeypatch.setattr(c16, 'drop_staging', lambda: None)
    monkeypatch.setattr(c16, 'merge', lambda **k: 0)

    def boom(*a, **k):
        raise KeyboardInterrupt('收到终止信号 15')

    monkeypatch.setattr(c16, 'scan', boom)
    monkeypatch.setattr(c16.signal, 'signal', lambda *a: None)   # 测试进程里别真接管信号
    monkeypatch.setattr('sys.argv', ['c16_minute', '--date', '2026-09-18'])

    assert c16.main() == 130
    assert [(b, s) for b, s, _ in calls] == [(777, 'failed')], '中断不许记 ok'
    assert '中断' in calls[0][2]
    out = capsys.readouterr().out
    assert '没有' in out and '当成成功' in out          # 人看的这句必须打出来


def test_已齐整日的文件重跑时跳过_没齐的要重来(monkeypatch):
    seen = {}
    rows = [{'security_code': '600000.SH'},        # 240 根 → 齐
            {'security_code': '688981.SH'}]

    def fake_query(sql, params=None, **k):
        seen['sql'] = sql
        seen['params'] = params
        return rows

    monkeypatch.setattr(c16.engine, 'query', fake_query)
    landed = c16.landed_codes(1, dt.date(2026, 9, 23), dt.date(2026, 9, 23))
    assert landed == {'600000.SH', '688981.SH'}
    # 窗口是 [目标日, 目标日+1)：写死一天才会漏掉跨界的那几根
    assert seen['params'][0] == 1 and seen['params'][1] == dt.date(2026, 9, 23)
    assert seen['params'][2] == dt.date(2026, 9, 24)
    # 判据是"当日根数够了"而不是"有没有行" —— 只进了一半的日子必须重解
    assert 'HAVING count(*) >= %s' in seen['sql']
    assert seen['params'][3] == c16.FULL_DAY_BARS[1]


def test_阈值按粒度分开():
    """1 分一天 240 根、5 分一天 48 根 —— 一个阈值会把 5 分全判成"没齐"。"""
    assert c16.FULL_DAY_BARS[1] == 200 and c16.FULL_DAY_BARS[5] == 40
