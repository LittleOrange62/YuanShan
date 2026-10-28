# -*- coding: utf-8 -*-
"""评分历史回算驱动（`ingest/jobs/d2b_score_backfill.py`）的回归。

钉的是三件"错了不报错、只会白跑或抢盘"的事：

1. **可续跑**：已经算过的场必须被跳过 —— 这批要跑几百场，中途被杀是常态而不是例外。
   判据来自库里"有非 stale 的 `oper_score` 行"，不靠台账（台账只留最近 30 条流水）。
2. **分片**：`--shard i/n` 各片互不相交、并集等于全集，否则两片在同一天上白抢。
3. **让路**：15:30~20:30（收盘链 + 每日备份的窗口）不动手；
   而且**不许把"看不见链在跑"当成"链没在跑"** —— `jobs.stage_busy()` 是进程内的 `_claims`，
   本驱动是另一个进程，用它判就是假绿灯，所以这里用的是确定性的时间窗。

外加写侧那条并行前提：中间表必须每张进程一张（`stg_score_<pid>`），
这是 2026-10-14 `indicators.py:137-143` 那起"两个写入者互相拆台"的同一课。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.compute import scoring
from ingest.jobs import d2b_score_backfill as B

D0 = dt.date(2025, 8, 28)
D_TO = dt.date(2026, 7, 28)
SESSIONS = [D0 + dt.timedelta(days=i) for i in range(0, 220, 1)]        # 测试里用连续日，日历形状不重要


def _days(n):
    return [d for d in SESSIONS if d <= D_TO][:n]


# ---------------------------------------------------------------- 一、分片与跳过

def test_分片互不相交且并集等于全集():
    days = _days(20)
    shards = [B.pending_dates(D0, D_TO, shard=i, shards=3, sessions=days, done=set())
              for i in range(3)]
    assert [len(s) for s in shards] == [7, 7, 6]
    assert len(set().union(*[set(s) for s in shards])) == 20
    assert not (set(shards[0]) & set(shards[1])) and not (set(shards[1]) & set(shards[2]))
    assert sorted(shards[0] + shards[1] + shards[2]) == sorted(days)


def test_已经算过的场必须被跳过():
    days = _days(6)
    left = B.pending_dates(D0, D_TO, sessions=days, done={days[1], days[4]})
    assert left == [days[0], days[2], days[3], days[5]], "续跑全靠这一条，跳过判据坏了就是重算几百场"


def test_区间外的日子一律不进来():
    days = [dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2026, 7, 28), dt.date(2027, 1, 4)]
    assert B.pending_dates(D0, D_TO, sessions=days, done=set()) == [dt.date(2026, 7, 28)]


# ---------------------------------------------------------------- 二、让路

def test_窗口里不动手并且等到窗口外才继续(monkeypatch):
    """假时钟：15:35 进来 → 必须先让路，睡到窗口外才动手。

    时钟由 `sleeper` 推着走（睡 300 秒就把钟拨 300 秒）—— 不推时钟的写法会让"等"变成死循环，
    而那条死循环在生产里恰好是**对的**（它就该等着），所以只有在单测里才需要假装时间过去了。
    """
    seen, slept = [], []
    now = {'t': dt.datetime(2026, 10, 19, 15, 35)}

    def sleeper(sec):
        slept.append(sec)
        now['t'] += dt.timedelta(seconds=sec)

    monkeypatch.setattr(B, 'jstore', type('J', (), {'record': staticmethod(lambda *a, **k: None)})())
    out = B.run(D0, D_TO, sessions=[dt.date(2026, 7, 27), dt.date(2026, 7, 28)], done=set(),
                clock=lambda: now['t'], sleeper=sleeper,
                compute=lambda d: seen.append(d) or {'codes': 10, 'score_rows': 3})
    assert out['yielded'] == 1, out
    assert any(s == 300.0 for s in slept), slept           # 等 = 睡 300 秒一轮，不是空转
    assert now['t'].hour >= 20, now['t']
    assert seen == [dt.date(2026, 7, 27), dt.date(2026, 7, 28)]
    assert out['ran'] == 2


def test_窗口判据是含起点不含终点():
    assert B.in_busy_window(dt.datetime(2026, 10, 19, 15, 30)) is True
    assert B.in_busy_window(dt.datetime(2026, 10, 19, 20, 29)) is True
    assert B.in_busy_window(dt.datetime(2026, 10, 19, 20, 30)) is False
    assert B.in_busy_window(dt.datetime(2026, 10, 19, 9, 0)) is False


# ---------------------------------------------------------------- 三、失败与记账

def test_一场坏了不带崩整批且每场都留一笔账(monkeypatch):
    rows = []
    fake = type('J', (), {'record': staticmethod(
        lambda key, ok, detail=None: rows.append((key, ok, detail)))})()
    monkeypatch.setattr(B, 'jstore', fake)
    days = [dt.date(2026, 7, 25), dt.date(2026, 7, 26), dt.date(2026, 7, 27)]

    def boom(d):
        if d == days[1]:
            raise ZeroDivisionError('输入面板空')
        return {'codes': 7, 'score_rows': 7}

    out = B.run(D0, D_TO, sessions=days, done=set(), clock=lambda: dt.datetime(2026, 10, 19, 10, 0),
                sleeper=lambda s: None, compute=boom)
    assert out['ran'] == 2 and len(out['failed']) == 1, out
    assert out['failed'][0]['date'] == str(days[1]) and 'ZeroDivisionError' in out['failed'][0]['error']
    assert [r[1] for r in rows] == [True, False, True], "失败那一场的账必须当场落下，否则就是跑过了没人知道"
    assert all(r[0] == 'score_backfill' for r in rows)
    assert rows[1][2]['reason'].startswith('ZeroDivisionError'), rows[1][2]


def test_没有输入日期时不许悄悄动手(monkeypatch, capsys):
    monkeypatch.setattr(B, 'sec', type('S', (), {                       # noqa: E731
        'latest_trade_date': staticmethod(lambda: dt.date(2026, 7, 28))})())
    monkeypatch.setattr('sys.argv', ['d2b_score_backfill', '--to', '2026-07-28'])
    assert B.main() == 2
    assert '--from' in capsys.readouterr().out


# ---------------------------------------------------------------- 四、并行前提：中间表按进程分

def test_中间表每张进程一张且upsert引用的是它():
    """`_write()` 一进门就 DROP 这张表。共用一张 ＝ 两个写入者互相拆台
    （2026-10-14 的 `d1_indicators` 事故：84 场回算跑到第 31 场被常驻服务把表抽走）。"""
    import os
    assert scoring._stage_table(scoring.STAGE_BASE_SCORE) == f'market_data.stg_score_{os.getpid()}'
    assert scoring._stage_table(scoring.STAGE_BASE_DETAIL).endswith(str(os.getpid()))
    assert '{stage}' in scoring.SCORE_UPSERT and '{stage}' in scoring.DETAIL_UPSERT
    assert 'market_data.stg_score\n' not in scoring.SCORE_UPSERT, '又写回固定名了'
    assert 'stg_score_detail' not in scoring.DETAIL_UPSERT.replace(
        scoring.DETAIL_UPSERT.format(stage='x'), '')
    q = scoring.SCORE_UPSERT.format(stage=scoring._stage_table(scoring.STAGE_BASE_SCORE))
    assert str(os.getpid()) in q and '{' not in q, "format 之后不许留花括号"


def test_评分表没有版本维所以归档表必须在():
    """`analytics.score` 没有 `spec_version`、写入是原地 upsert ⇒ 重算前不归档就是永久丢掉对照基准。
    这条钉的是"归档表还在"，不是"我归档了"（归档动作本身跑没跑看 `operations.*` 与台账）。"""
    cols = {r['column_name'] for r in __import__('app.db.engine', fromlist=['engine']).query(
        "select column_name from information_schema.columns "
        "where table_schema='analytics' and table_name='score'", role='reader')}
    assert 'spec_version' not in cols, 'score 加了版本维的话，本文件的整段前提都要重看'
    arch = __import__('app.db.engine', fromlist=['engine']).query(
        "select count(*) n from information_schema.columns "
        "where table_schema='analytics' and table_name='score_legacy_archive'", role='reader')
    assert arch[0]['n'] == 12, '归档表不在了（DDL 45）'
