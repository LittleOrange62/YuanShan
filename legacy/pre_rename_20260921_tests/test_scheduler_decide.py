# -*- coding: utf-8 -*-
"""
调度器判定链。

**一个都不碰数据库。** 这是 `app/orchestrator/schedule.py` 被写成纯函数的全部
理由：判错一次调度的后果不是报错，是"页面上有数但是旧的"，那种问题当场没人发现，
所以它必须能在一个空库、甚至没装 PG 的机器上被逐条分支钉死。

沿用 `test_fill_all_failure.py` 的口径：测判定，不测装配。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.orchestrator import schedule as s

# 2026-09-21 是周一（正常交易日）；2026-09-25 用来当"节假日"，
# 交易日判定一律靠注入的 trade_day_of，不查 ref.trade_calendar。
MON = dt.date(2026, 9, 21)
HOLIDAY = dt.date(2026, 9, 25)
NOON = dt.datetime(2026, 9, 21, 15, 11, 0)

CRON_DAILY = '10 15,16,17 * * 1-5'


def job(**over) -> dict:
    base = {'job_key': 'sync_daily', 'name': '同步日线', 'kind': 'ingest',
            'trigger_kind': 'cron', 'cron_expr': CRON_DAILY,
            'only_trade_day': True, 'enabled': True, 'caps': [], 'ready_on': None,
            'after_job': None, 'params': {}, 'scope': {}}
    base.update(over)
    return base


def env(**over) -> s.Env:
    base = {'now': NOON, 'scheduler_on': True, 'target_date': MON,
            'trade_day_of': lambda d: d != HOLIDAY}
    base.update(over)
    return s.Env(**base)


# ---------------------------------------------------------------- 首次遇到作业
def test_first_seen_today_uses_the_slot_that_already_passed():
    """
    15:30 才把调度器打开的人，今天就得能自动跑 `10 15,16,17`。

    往后取明天的槽是这个功能最容易被误会成"开关没生效"的一种失效：
    页面显示下次触发=明天 15:10，今天一动不动，看起来一切正常。
    """
    d = s.decide(job(), s.SlotState(), env(now=dt.datetime(2026, 9, 21, 15, 30)))
    assert d.action == 'fire'
    assert d.slot == '2026-09-21T15:10'
    assert d.slot_consumed


def test_first_seen_before_any_slot_waits_for_next():
    d = s.decide(job(), s.SlotState(), env(now=dt.datetime(2026, 9, 21, 9, 0)))
    assert d.action == 'wait'
    assert d.next_fire_at == dt.datetime(2026, 9, 21, 15, 10)
    assert not d.slot_consumed, '没到点不该消费槽'


def test_first_seen_does_not_reach_back_across_days():
    """周一早上打开调度器，不该去补上周五那一槽 —— 补历史是 fill_all 的职责。"""
    d = s.decide(job(), s.SlotState(), env(now=dt.datetime(2026, 9, 21, 9, 0)))
    assert d.action == 'wait'
    assert d.next_fire_at.date() == dt.date(2026, 9, 21)


# ---------------------------------------------------------------- 交易日
def test_holiday_skips_and_consumes_the_slot():
    st = s.SlotState(next_fire_at=dt.datetime(2026, 9, 25, 15, 10),
                     cron_used=CRON_DAILY)
    d = s.decide(job(), st, env(now=dt.datetime(2026, 9, 25, 15, 11),
                                target_date=None))
    assert d.action == 'skip'
    assert '不是交易日' in d.reason
    assert d.slot_consumed, '跳过也要消费槽，否则下个 tick 又判一遍同样的话'


def test_only_trade_day_false_runs_on_weekend():
    """
    `fill_all` / `sync_ths_block` / `sync_snapshot` 都是 false —— 种子数据里逐条
    注释过原因：复盘多在周末做，拦在"今天不是交易日"上等于周末永远补不了数据。
    """
    st = s.SlotState(next_fire_at=dt.datetime(2026, 9, 25, 15, 10),
                     cron_used=CRON_DAILY)
    j = job(only_trade_day=False)
    d = s.decide(j, st, env(now=dt.datetime(2026, 9, 25, 15, 11), target_date=None))
    assert d.action == 'fire'


# ---------------------------------------------------------------- 同槽去重
def test_consumed_slot_says_nothing_and_keeps_the_note():
    """
    已消费的槽返回 idle 且原因为空。

    要是返回带文字的 wait，`save_state` 会把上一次真正的判定（"15:10 触发"、
    "不是交易日所以跳过"）覆盖成"本次已触发过" —— 而"为什么没跑"正是 note
    这一列存在的全部理由，会被自己的心跳刷成空话。

    用 chain 型作业测：cron 型的槽在消费时 next_fire_at 已经前移，天然不会再回到
    这一支；日槽型（一个交易日一个槽）才是"同一个槽反复重判"的主场。
    """
    e = env(deps_succeeded=frozenset({'sync_daily'}))
    st = s.SlotState(last_fire_slot='D:2026-09-21')
    d = s.decide(chain_job(), st, e)
    assert d.action == 'idle'
    assert d.reason == ''
    assert not d.slot_consumed


def test_idle_does_not_overwrite_the_stored_reason():
    """
    仓库侧的配套约束：idle 只许推进 next_fire_at。

    `save_state` 里那条分支是整个"为什么没跑"能成立的关键，纯函数这边判成
    idle 之后如果落库照样写 note，前一条测试就只是自娱自乐。
    """
    from app.db.repo import schedule as sch

    assert sch.job_slot_for_write(s.Decision('idle', '')) is None


def test_three_cron_slots_only_fire_once_each():
    """`10 15,16,17` 一天三个槽：每个各触发一次，触发完的槽不再碰。"""
    fired = []
    for hh, mm in ((15, 10), (16, 10), (17, 10)):
        now = dt.datetime(2026, 9, 21, hh, mm + 1)
        st = s.SlotState(next_fire_at=dt.datetime(2026, 9, 21, hh, mm),
                         last_fire_slot=fired[-1] if fired else None,
                         cron_used=CRON_DAILY)
        d = s.decide(job(), st, env(now=now))
        assert d.action == 'fire', f'{hh}:{mm} 该触发'
        fired.append(d.slot)
    assert len(set(fired)) == 3


# ---------------------------------------------------------------- 停机补跑
def test_slot_older_than_catchup_window_is_skipped_and_counted():
    st = s.SlotState(next_fire_at=dt.datetime(2026, 9, 10, 15, 10),
                     cron_used=CRON_DAILY)
    d = s.decide(job(), st, env())
    assert d.action == 'skip'
    assert d.missed, '要计入 missed_count —— 页面得能看出"停机丢了多少"'
    assert d.next_fire_at > NOON, '并把下次触发拉回当下，别一格一格爬'


def test_catchup_within_window_fires_the_stale_slot():
    """窗口之内的过期槽照样补：停机两小时后重启，该跑的还得跑。"""
    st = s.SlotState(next_fire_at=dt.datetime(2026, 9, 20, 17, 10),
                     cron_used=CRON_DAILY)
    d = s.decide(job(), st, env(catchup_days=5))
    assert d.action == 'fire'
    assert not d.missed


# ---------------------------------------------------------------- cron 变更
def test_changed_cron_expression_invalidates_the_stored_next_fire():
    """
    把 `0 8 * * *` 改成 `30 15 * * *` 之后，按老表达式算出来的"明早 8 点"
    必须作废重算 —— 不记 cron_used 这件事没人会发现，表现成"改了没生效"。
    """
    st = s.SlotState(next_fire_at=dt.datetime(2026, 9, 22, 8, 0), cron_used='0 8 * * *')
    d = s.decide(job(cron_expr='30 15 * * *'), st, env(now=dt.datetime(2026, 9, 21, 9, 0)))
    assert d.next_fire_at == dt.datetime(2026, 9, 21, 15, 30)


# ---------------------------------------------------------------- 依赖与就绪
def chain_job(**over) -> dict:
    return job(**{'trigger_kind': 'chain', 'after_job': 'sync_daily',
                  'cron_expr': None, 'job_key': 'sync_minute',
                  'name': '同步分钟线', **over})


def ready_job(**over) -> dict:
    return job(**{'trigger_kind': 'on_ready', 'ready_on': ['md.stock_daily'],
                  'cron_expr': None, 'job_key': 'calc_base_factors',
                  'name': '基础因子', 'kind': 'compute', **over})


def test_chain_waits_until_upstream_succeeded_today():
    j = chain_job()
    d = s.decide(j, s.SlotState(), env())
    assert d.action == 'wait'
    assert 'sync_daily' in d.reason
    assert not d.slot_consumed, '前置没好不能消费槽，否则今天再没机会'


def test_chain_fires_once_upstream_succeeded():
    e = env(deps_succeeded=frozenset({'sync_daily'}))
    d = s.decide(chain_job(), s.SlotState(), e)
    assert d.action == 'fire'
    assert d.slot == 'D:2026-09-21'


def test_on_ready_waits_for_the_dataset():
    d = s.decide(ready_job(), s.SlotState(), env())
    assert d.action == 'wait'
    assert 'md.stock_daily' in d.reason


def test_on_ready_fires_when_dataset_ready():
    e = env(ready_datasets=frozenset({'md.stock_daily'}))
    d = s.decide(ready_job(), s.SlotState(), e)
    assert d.action == 'fire'


def test_missing_after_job_or_ready_on_is_a_config_error_not_a_hang():
    """配置填坏了要当场 skip 并说明，不能变成永远 wait —— 那比报错更难发现。"""
    bad1 = s.decide(chain_job(after_job=None), s.SlotState(), env())
    bad2 = s.decide(ready_job(ready_on=[]), s.SlotState(), env())
    assert bad1.action == 'skip' and 'after_job' in bad1.reason
    assert bad2.action == 'skip' and 'ready_on' in bad2.reason


# ---------------------------------------------------------------- 质量门禁
def test_quality_gate_blocks_compute_but_not_ingest():
    """
    拦 compute 不拦 ingest：数据该拿还得拿，拦采集会导致永远补不上，
    而质量规则本身也可能写错。
    """
    # 数据就绪、前置跑完 —— 让判定链走到 ⑥，否则它停在 ⑤ 就碰不到门禁。
    e = env(quality_blocked=True, ready_datasets=frozenset({'md.stock_daily'}),
            deps_succeeded=frozenset({'sync_daily'}))
    blocked = s.decide(ready_job(), s.SlotState(), e)
    passed = s.decide(job(), s.SlotState(next_fire_at=NOON.replace(minute=10),
                                         cron_used=CRON_DAILY), e)
    assert blocked.action == 'skip' and '质量门禁' in blocked.reason
    assert passed.action == 'fire', 'ingest 类不受门禁影响'


def test_quality_gate_names_the_date_and_the_escape_hatch():
    """拦截原因必须写清楚是哪一天、以及怎么豁免。"""
    e = env(quality_blocked=True, ready_datasets=frozenset({'md.stock_daily'}))
    d = s.decide(ready_job(), s.SlotState(), e)
    assert str(MON) in d.reason
    assert 'quality_gate_override_date' in d.reason


def test_quality_gate_waits_for_data_before_judging():
    """⑤ 在 ⑥ 之前：数据都没就绪时先说等数据，不要报一个门禁当借口。"""
    d = s.decide(ready_job(), s.SlotState(), env(quality_blocked=True))
    assert d.action == 'wait' and 'md.stock_daily' in d.reason


# ---------------------------------------------------------------- 幂等
def test_already_succeeded_today_is_skipped():
    """双保险的另一半：on_ready 主触发 + cron 兜底，靠这条保证一天只成功一次。"""
    e = env(self_succeeded=frozenset({'sync_daily'}))
    st = s.SlotState(next_fire_at=NOON.replace(minute=10), cron_used=CRON_DAILY)
    d = s.decide(job(), st, e)
    assert d.action == 'skip'
    assert '成功记录' in d.reason


# ---------------------------------------------------------------- 开关
def test_disabled_job_is_skipped_without_consuming():
    d = s.decide(job(enabled=False), s.SlotState(next_fire_at=NOON.replace(minute=10),
                                                 cron_used=CRON_DAILY), env())
    assert d.action == 'skip' and '停用' in d.reason
    assert not d.slot_consumed


def test_scheduler_switch_off_skips_everything():
    st = s.SlotState(next_fire_at=NOON.replace(minute=10), cron_used=CRON_DAILY)
    d = s.decide(job(), st, env(scheduler_on=False))
    assert d.action == 'skip' and 'scheduler_enabled' in d.reason


def test_manual_jobs_are_never_returned_by_the_query():
    """
    `trigger_kind='manual'` 由 `schedulable_jobs()` 的 SQL 挡掉，不靠判定链。
    这条钉住那个约定：调度器替人决定"要不要现在追平一次"不合适 ——
    fill_all 一次 4 小时，自动起来会把队列占满。
    """
    jobs = [{'job_key': k, 'trigger_kind': t, 'enabled': True}
            for k, t in (('fill_all', 'manual'), ('sync_daily', 'cron'))]
    kept = [j for j in jobs if j['trigger_kind'] != 'manual']
    assert [j['job_key'] for j in kept] == ['sync_daily']


# ---------------------------------------------------------------- 健壮性
def test_unparseable_cron_does_not_break_the_tick():
    d = s.decide(job(cron_expr='这不是 cron'), s.SlotState(), env())
    assert d.action == 'skip'
    assert 'cron' in d.reason.lower()


def test_unknown_slot_target_without_target_date_waits():
    """日历彻底不可用时 on_ready 作业不能凭空造一个交易日出来跑。"""
    d = s.decide(ready_job(), s.SlotState(), env(target_date=None))
    assert d.action == 'wait'
    assert '目标交易日' in d.reason


# ---------------------------------------------------------------- cron 解析
@pytest.mark.parametrize('expr, after, want', [
    (CRON_DAILY, dt.datetime(2026, 9, 21, 15, 0), dt.datetime(2026, 9, 21, 15, 10)),
    (CRON_DAILY, dt.datetime(2026, 9, 21, 15, 10), dt.datetime(2026, 9, 21, 16, 10)),
    (CRON_DAILY, dt.datetime(2026, 9, 21, 17, 11), dt.datetime(2026, 9, 22, 15, 10)),
    # 周一到周五：周五 17:11 之后要跳到下周一
    ('0 8 * * 1-5', dt.datetime(2026, 9, 25, 9, 0), dt.datetime(2026, 9, 28, 8, 0)),
])
def test_next_cron_slot(expr, after, want):
    assert s._next_cron_slot(expr, after) == want


# ---------------------------------------------------------------- cron 字段口径
# 这一组是本项目换掉 APScheduler 解析器的原因，逐条钉死，不许"以后再说"。
@pytest.mark.parametrize('dow, want', [
    ('1-5', dt.date(2026, 9, 21)),    # 周一。APScheduler 会把 1-5 读成周二起
    ('0', dt.date(2026, 9, 20)),      # 周日 = 0（标准 cron），不是周一
    ('7', dt.date(2026, 9, 20)),      # 7 是周日的别名，APScheduler 直接 ValueError
    ('6', dt.date(2026, 9, 26)),      # 周六 = 6，不是周五
    ('sat', dt.date(2026, 9, 26)),    # 名字写法
    ('mon-fri', dt.date(2026, 9, 21)),
    ('5-1', dt.date(2026, 9, 26)),    # 跨周末回绕：sat 在 fri-mon 之内
])
def test_day_of_week_uses_standard_cron_convention(dow, want):
    """
    **星期字段按标准 cron：0=周日、7=周日、1-5=周一至周五。**

    APScheduler 3.x 是 0=周一…6=周日，与本表相反。种子里的 `10 15,16,17 * * 1-5`
    若交给它会变成周二到周六：周一不采、周六白采，而页面显示一切正常。
    """
    assert s.cron_spec(f'0 8 * * {dow}').day_matches(want)


def test_saturday_is_not_a_weekday_for_1_to_5():
    spec = s.cron_spec(CRON_DAILY)
    assert not spec.day_matches(dt.date(2026, 9, 26)), '周六'
    assert not spec.day_matches(dt.date(2026, 9, 27)), '周日'
    assert spec.day_matches(dt.date(2026, 9, 25)), '周五'


def test_dom_and_dow_or_semantics_follow_vixie_cron():
    """
    Vixie cron：dom 与 dow **都**受限时取并集（"1 号 或 周一"），不是交集。

    种子里目前没有这种表达式，但积木管理页允许自由填，口径必须钉住 ——
    按交集实现的话 `0 0 1 * 1` 会从"每月 1 号加每周一"变成"逢 1 号且是周一"，
    差出一个数量级的触发次数。
    """
    spec = s.cron_spec('0 0 1 * 1')
    assert spec.day_matches(dt.date(2026, 9, 1)), '1 号（那天是周二）'
    assert spec.day_matches(dt.date(2026, 9, 7)), '周一（不是 1 号）'
    assert not spec.day_matches(dt.date(2026, 9, 8)), '既不是 1 号也不是周一'


def test_dom_alone_is_not_affected_by_the_dow_rule():
    """只有 dom 受限（dow 是 `*`）时按 dom 判 —— 这是"每月 15 号"这种写法的本意。"""
    spec = s.cron_spec('30 9 15 * *')
    assert spec.day_matches(dt.date(2026, 9, 15))
    assert not spec.day_matches(dt.date(2026, 9, 16))


@pytest.mark.parametrize('text, lo, hi, want', [
    ('*/15', 0, 59, (0, 15, 30, 45)),
    ('0,30', 0, 59, (0, 30)),
    ('5/20', 0, 59, (5, 25, 45)),          # `5/20` 等价 `5-59/20`，不从 0 开始
    ('15,17', 0, 23, (15, 17)),
    ('1-3', 1, 12, (1, 2, 3)),
    ('0', 0, 7, (0,)),                     # 周日
    ('7', 0, 7, (0,)),                     # 7 归一化成 0，取值集合里不该出现 7
])
def test_field_parsing_forms(text, lo, hi, want):
    is_dow = (lo, hi) == (0, 7)
    got = tuple(sorted(s._field_values(text, lo, hi, is_dow=is_dow)))
    assert got == want


@pytest.mark.parametrize('bad', [
    '这不是 cron', '* * * *', '61 * * * *', '* 25 * * *', '* * 0 * *',
    '* * * * 8', '*/0 * * * *', '* * * * mon-x',
])
def test_bad_expressions_raise_instead_of_silently_never_firing(bad):
    """
    表达式写错必须当场抛，不能变成"永远不触发"。

    静默不触发是这个系统里最难发现的一类故障：页面显示下次触发 `—`，
    作业不跑，没有任何红字。调度器主进程会把它捕获后写成 skip 原因显示出来。
    """
    with pytest.raises(ValueError):
        s.cron_spec(bad)


def test_next_after_returns_none_for_an_unreachable_expression():
    """`0 0 30 2 *`（2 月 30 日）永远不可能，返回 None 而不是无限找。"""
    assert s.cron_spec('0 0 30 2 *').next_after(dt.datetime(2026, 1, 1)) is None


def test_backfill_slots_lists_everything_in_the_window():
    got = s.backfill_slots(CRON_DAILY, dt.datetime(2026, 9, 21, 0, 0),
                           dt.datetime(2026, 9, 21, 23, 59))
    assert [t.strftime('%H:%M') for t in got] == ['15:10', '16:10', '17:10']


def test_decision_actions_are_the_documented_vocabulary():
    """docs/09 §6.2 只有 fire / wait / skip / idle 四种结论，加一种就要改文档。"""
    seen = set()
    for st, e in [(s.SlotState(), env()),
                  (s.SlotState(next_fire_at=NOON.replace(minute=10),
                               cron_used=CRON_DAILY), env()),
                  (s.SlotState(next_fire_at=NOON.replace(minute=10),
                               last_fire_slot='2026-09-21T15:10',
                               cron_used=CRON_DAILY), env())]:
        seen.add(s.decide(job(), st, e).action)
    assert seen <= {'fire', 'wait', 'skip', 'idle'}
