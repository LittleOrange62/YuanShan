# -*- coding: utf-8 -*-
"""
质量审计：缺口判定 + 规则真的能跑。

要锁住的是 2026-08-30 用户点出来的那个问题：页面上写着「最近一次 2026-08-26
17:54」，而那其实是**迁移对账**的时间戳 —— 24 条规则登记在册，但没有任何执行器
按交易日跑过。看板一片绿、实际什么都没查，是这套系统最不能接受的一类缺陷。

所以这里断言的是结果，不是过程：
- `gap()` 的目标日 = 当前北京时间往前推的最近一个**已收盘**交易日
- 每一条启用的规则都能真的执行（SQL 写坏了当场红，而不是等某天静默漏过）
- `ops.quality_result` 已经审计到目标日（跑过一次 e2 之后应当如此）
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.db import engine
from app.orchestrator import runner, tradeday
from app.quality import audit


# ------------------------------------------------------------ 占位符替换
def test_prepare_replaces_only_standalone_date_placeholder():
    # `::date` 里的 `::d` 不是占位符。裸 replace(':d') 会把它换掉，
    # minute_daily_consistent 那条规则第一版就是这么废的。
    sql = "SELECT (m.dt AT TIME ZONE 'Asia/Shanghai')::date = :d"
    out = audit._prepare(sql)
    assert '::date' in out
    assert out.endswith('%(d)s')
    assert out.count('%(d)s') == 1


def test_prepare_escapes_literal_percent():
    out = audit._prepare("SELECT x LIKE 'a%' AND d = :d")
    assert "'a%%'" in out and '%(d)s' in out


def test_prepare_handles_interval_arithmetic():
    out = audit._prepare("WHERE trade_date > :d - interval '40 days'")
    assert "%(d)s - interval '40 days'" in out


# ------------------------------------------------------------ 每条规则都能跑
def test_every_enabled_rule_actually_runs():
    """
    规则表是配置，但配置里写的是 SQL —— 写坏了只有真跑一次才知道。

    判据是"不抛异常"，不是"通过"：通不过是数据问题（该在页面上红），
    跑不起来是规则问题（该在这里红）。
    """
    d = engine.scalar('SELECT max(trade_date) FROM md.stock_daily')
    if d is None:
        pytest.skip('库里还没有日线')
    rep = audit.audit_day(d, save=False)
    broken = [r['rule_key'] for r in rep['results']
              if (r['actual'] or {}).get('error')]
    assert not broken, f'这些规则自己跑不起来：{broken}'
    assert rep['total'] >= 20, '启用的规则数不对劲'


def test_builtin_rules_point_at_objects_that_exist():
    """`check_kind='builtin'` 的规则靠库层约束/触发器兜底，约束被 DROP 了要红。"""
    for r in audit.rules():
        if r['check_kind'] != 'builtin':
            continue
        passed, actual = audit._run_builtin(r, dt.date.today())
        assert passed, f"{r['rule_key']}：库里找不到 {r['check_body']}"
        assert actual['copies'] >= 1


# ------------------------------------------------------------ 缺口判定
def test_target_is_the_last_closed_trade_day():
    g = audit.gap()
    assert g['target'] is not None
    assert g['target'] <= tradeday.closed_cutoff(g['now'])
    assert engine.scalar('SELECT is_open FROM ref.trade_calendar '
                         ' WHERE cal_date = %s', (g['target'],)) is True


def test_cutoff_uses_the_1530_line():
    """14:00 点开页面，"最近已收盘"是昨天；15:30 之后才轮到今天。"""
    noon = dt.datetime(2026, 8, 28, 14, 0)
    late = dt.datetime(2026, 8, 28, 15, 30)
    assert tradeday.closed_cutoff(noon) == dt.date(2026, 8, 27)
    assert tradeday.closed_cutoff(late) == dt.date(2026, 8, 28)


def test_missing_days_are_bounded_and_ordered():
    g = audit.gap(back=5)
    assert len(g['missing']) <= 5, '回补窗口没有被 back 限住'
    assert g['missing'] == sorted(g['missing']), '要按日期升序逐天补'
    assert all(d <= g['target'] for d in g['missing'])


def test_days_without_quotes_are_reported_not_audited():
    """没有行情的交易日只报"还没采"，不能算成"质量不合格"。"""
    g = audit.gap()
    for d in g['no_data']:
        assert not audit.has_data(d)
    for d in g['runnable']:
        assert audit.has_data(d)


# ------------------------------------------------------------ 结果
def test_audit_has_caught_up_to_the_target_day():
    """
    ★这条就是用户那句话的验收：页面上的「最近一次」必须意味着
    "最新已收盘交易日体检过了"，不是某次迁移的时间戳。
    """
    g = audit.gap()
    assert g['audited_max'] is not None, \
        'ops.quality_result 里没有任何按交易日的审计记录 —— 跑一次 e2_quality_audit.py'
    assert not g['runnable'], f"还有没审的交易日：{g['runnable']}"
    assert g['audited_max'] == g['target']


def test_results_are_recorded_per_day_not_lumped_together():
    d = audit.audited_max()
    n = engine.scalar(
        'SELECT count(DISTINCT rule_key) FROM ops.quality_result '
        ' WHERE trade_date = %s', (d,))
    assert n >= 20, f'{d} 只落了 {n} 条规则结果'


# ------------------------------------------------------------ 作业绑定
def test_job_is_wired_to_a_real_runner():
    """声明与实现必须同批：作业存在但没有执行器等于按钮点了什么都不做。"""
    assert 'quality_audit' in runner.RUNNERS
    job = engine.query_one(
        'SELECT * FROM config.job WHERE job_key = %s', ('quality_audit',))
    assert job is not None
    assert runner.job_runner(job) == 'quality_audit'
    # 周末也要能跑：它审的是"最近一个已收盘交易日"
    assert job['only_trade_day'] is False
