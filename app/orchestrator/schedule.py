# -*- coding: utf-8 -*-
"""
调度器的判定链。**纯函数、不接数据库、不取系统时间。**

单独成模块的理由和 `tradeday.py` 一样：判据必须只有一份。区别在于这里连 DB
都不碰 —— `decide()` 要能在一个空库的机器上、在一台没装 PG 的机器上被完整测过。
判错一次调度的代价不是报错，是"页面上有数但是旧的"，那种问题没人当场发现。

## 为什么自己写 cron 解析，而不是用已经装好的 APScheduler

`docs/09 §4.1` 原本写的是"只借 `apscheduler.triggers.cron.CronTrigger` 当解析器"。
实测（2026-09-21）把这个方案否了：

    CronTrigger.from_crontab('10 15 * * 1-5')  →  下一次触发是**周二**
    CronTrigger.from_crontab('10 15 * * 0-4')  →  下一次触发是周一
    CronTrigger.from_crontab('10 15 * * 7')    →  ValueError: 7 > 6

**APScheduler 的 `day_of_week` 是 0=周一…6=周日；标准 cron 是 0=周日、7=周日。**
而 `config.job` 里种的那批表达式（`10 15,16,17 * * 1-5`、`0 8 * * 1-5`）是按
标准 cron 写的。照原方案接上 APScheduler，`sync_daily` 会在**周二到周六**触发：
周一整天不采，周六白采一趟，而且页面上一切显示正常、cron 那一栏明明白白写着
`1-5`。这是本系统立项要消灭的那一类错误，不能靠"在 README 里提醒一句 0 是周一"
绕过 —— 那样下一个写种子的人照样会踩。

所以这里自己实现（`_CronSpec`，约 70 行），语义按 Vixie cron，
`tests/test_scheduler_decide.py` 用真实日期把每个分支钉死。
APScheduler 依赖保留在 `pyproject.toml` 里但不再被使用，是否清出去见 docs/09 §11。

判定的**顺序是有原因的**，不要重排：

  ①  作业停用 / 调度总开关关         —— 先看有没有资格被调度，不看时间
  ②  触发槽到期                     —— cron 负责回答"几点"
  ③  该槽是否已消费                 —— 同槽只消费一次
  ④  是不是交易日                   —— **必须在 ② 之后**
  ⑤  链式依赖 / 数据就绪             —— 未满足是 wait（下个 tick 再看），不是 skip
  ⑥  质量门禁                       —— blocking 规则未过则拦下 compute 类作业
  ⑦  当日已有成功记录               —— 幂等，双保险的另一半
  ⑧  入队

④ 为什么在 ② 后面：cron 表达式自己不带节假日知识。`10 15,16,17 * * 1-5` 里那个
`1-5` 只是粗筛，真正"国庆算不算交易日"由 `reference_data.trade_calendar` 回答。旧
`scheduler.py:229-242` 的毛病就是算了 `latest`/`today` 然后三个 return 全写
`weekday() < 5`，节假日照样触发。两问分开答才查得出是谁判错的。

`fire` / `wait` / `skip` / `idle` 四种结果是有意区分的：

  fire   入队执行，槽被消费，next_fire_at 前移
  wait   槽**不**消费，下个 tick 拿同一个槽再判一次 —— 用于"前置还没跑完"
  skip   槽被消费、next_fire_at 前移，但不执行，并留下原因 —— 用于"这个槽本不该跑"
  idle   这个槽已经处理完了，本 tick 没有新信息 —— 只推进 next_fire_at，**不动 note**

wait 不能退化成 skip：`sync_minute` 挂在 `sync_daily` 后面，日线跑到 16:40 才完事，
中间每个 tick 都在 wait；要是判成 skip，分钟线今天就不会再有机会跑了。
idle 不能退化成 wait：带原因的 wait 会把上一次真正的判定覆盖成"本次已触发过"，
而"为什么没跑"正是页面那一列存在的全部理由。
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import dataclass

# 触发槽的字符串形态。精确到分钟 —— cron 最细就是分钟，带秒会让"同一分钟的两个
# tick"被当成两个槽，白跑一遍。
SLOT_FMT = '%Y-%m-%dT%H:%M'

# 一个 cron 槽最多 wait 多久。超过就当作来不及了，消费掉并记 skip 原因，
# 免得依赖永远等不到的作业把同一个槽占到底、页面上"等 sync_daily"挂一天。
DEFAULT_SLOT_TTL_SEC = 6 * 3600

# 质量门禁只拦这几类作业。**不拦 ingest**：数据该拿还得拿，
# 拦采集会导致永远补不上，而质量规则本身也可能写错（见 §6.4 的 override）。
GATED_KINDS = ('compute', 'report', 'quality')


def slot_of(when: dt.datetime) -> str:
    return when.strftime(SLOT_FMT)


_DOW_NAMES = {'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6}
# (下界, 上界, 是不是星期)。星期的上界写 7 是为了接受标准 cron 里"7 也等于周日"
# 这个别名，归一化在 `_field_values` 末尾做。
_FIELDS = (('minute', 0, 59, False), ('hour', 0, 23, False),
           ('dom', 1, 31, False), ('month', 1, 12, False),
           ('dow', 0, 7, True))


def _num(tok: str, *, is_dow: bool) -> int:
    tok = tok.strip().lower()
    if is_dow and tok[:3] in _DOW_NAMES:
        return _DOW_NAMES[tok[:3]]
    if not tok.isdigit():
        raise ValueError(f'无法解析的字段值：{tok!r}')
    return int(tok)


def _field_values(text: str, lo: int, hi: int, *, is_dow: bool) -> frozenset[int]:
    """
    解析 cron 的单个字段。支持 `*` `a` `a-b` `a-b/n` `*/n` `a,b,c` 及其混用。

    星期的口径按**标准 cron**：0=周日，7 也=周日，可写名字。这就是本模块不用
    APScheduler 解析器的原因，见文件开头那段实测。
    """
    out: set[int] = set()
    for part in (text or '').split(','):
        part = part.strip()
        if not part:
            raise ValueError('字段里有空的逗号项')
        base, _, step_s = part.partition('/')
        step = 1
        if step_s:
            if not step_s.isdigit() or int(step_s) < 1:
                raise ValueError(f'步进值不合法：{part!r}')
            step = int(step_s)
        if base == '*':
            spans = [(lo, hi)]
        elif '-' in base:
            a, _, b = base.partition('-')
            start, end = _num(a, is_dow=is_dow), _num(b, is_dow=is_dow)
            if start <= end:
                spans = [(start, end)]
            else:
                # `fri-mon` / `22-2` 这种跨周末、跨月的写法：拆成头尾两段。
                # 先归一化再比大小会把 fri(5)>mon(1) 这个事实丢掉，所以放在这里。
                spans = [(start, hi if not is_dow else 6), (lo, end)]
        else:
            v = _num(base, is_dow=is_dow)
            # `5/2` 在 Vixie cron 里等价于 `5-<上界>/2`，不是从 lo 开始
            spans = [(v, hi)] if step_s else [(v, v)]
        for start, end in spans:
            if not lo <= start <= hi or not lo <= end <= hi:
                raise ValueError(f'{part!r} 超出范围 {lo}-{hi}')
            for v in range(start, end + 1, step):
                out.add(0 if (is_dow and v == 7) else v)
    return frozenset(out)


class _CronSpec:
    """五段 cron。语义按 Vixie cron，时刻一律按北京时间。"""

    __slots__ = ('fields', 'dom_star', 'dow_star')

    def __init__(self, expr: str) -> None:
        parts = (expr or '').split()
        if len(parts) != 5:
            raise ValueError(f'cron 需要 5 个字段，收到 {len(parts)} 个：{expr!r}')
        self.fields = tuple(
            _field_values(parts[i], lo, hi, is_dow=is_dow)
            for i, (_name, lo, hi, is_dow) in enumerate(_FIELDS))
        # Vixie cron 的"日 OR 星期"规则：两个字段都做了限制时，满足任一即可。
        # 种子里目前没有既限 dom 又限 dow 的表达式，但积木页允许自由填，
        # 语义必须钉准而不是"以后再说"。
        self.dom_star = parts[2] == '*'
        self.dow_star = parts[4] == '*'

    def day_matches(self, d: dt.date) -> bool:
        (_minute, _hour, dom, month, dow) = self.fields
        if d.month not in month:
            return False
        by_dom = d.day in dom
        # Python 的 weekday() 是 0=周一；cron 是 0=周日，换算一下。
        by_dow = ((d.weekday() + 1) % 7) in dow
        if self.dom_star:
            return True if self.dow_star else by_dow
        if self.dow_star:
            return by_dom
        return by_dom or by_dow

    def next_after(self, after: dt.datetime) -> dt.datetime | None:
        """严格晚于 `after` 的第一个触发时刻（北京时间 naive）。"""
        (_minute, _hour, _dom, _month, _dow) = self.fields
        minutes, hours = sorted(_minute), sorted(_hour)
        # 400 天足够覆盖 `0 0 29 2 *`（闰年 2 月 29）这种最稀的表达式；
        # 再找不到就是表达式本身写错了，返回 None 让调用方报错，
        # 比无限循环或悄悄不跑都好。
        for offset in range(401):
            day = after.date() + dt.timedelta(days=offset)
            if not self.day_matches(day):
                continue
            for hh in hours:
                for mm in minutes:
                    when = dt.datetime.combine(day, dt.time(hh, mm))
                    if when > after:
                        return when
        return None


_specs: dict[str, _CronSpec] = {}


def cron_spec(expr: str) -> _CronSpec:
    """带缓存的解析。表达式来自表，种类有限，缓存不会漂。"""
    if expr not in _specs:
        _specs[expr] = _CronSpec(expr)
    return _specs[expr]


def _next_cron_slot(expr: str, after: dt.datetime) -> dt.datetime | None:
    """cron 表达式在 `after` 之后的第一个触发时刻，**北京时间 naive**。"""
    return cron_spec(expr).next_after(after)


@dataclass(frozen=True)
class SlotState:
    """调度器记忆里跟"下次什么时候该看这个作业"有关的那部分。"""
    next_fire_at: dt.datetime | None = None
    last_fire_slot: str | None = None
    consecutive_fail: int = 0
    # 算出 next_fire_at 时用的那个 cron_expr。与当前 config.job.cron_expr 不一致
    # 说明表达式被改过，旧算出来的 next_fire_at 必须作废重算。
    cron_used: str | None = None


@dataclass(frozen=True)
class Decision:
    action: str                     # 'fire' | 'wait' | 'skip' | 'idle'
    reason: str                     # 永远非空（idle 除外）。skip/wait 时它是要显示给人看的那句话
    slot: str | None = None         # 本次判定涉及的槽（消费或等待的对象）
    # 判定之后要写回 schedule_state 的下次触发时刻。None 表示不变。
    next_fire_at: dt.datetime | None = None
    slot_consumed: bool = False     # True → last_fire_slot 记为 slot，并推进 next_fire_at
    # True → 这个槽是被"停机太久"跳过的，missed_count 要 +1。
    # 单独一个布尔而不是从 reason 文字里判：文案会改，计数不能跟着漂。
    missed: bool = False


@dataclass(frozen=True)
class Env:
    """
    判定所需的**外部事实**，全部由调用方查好传进来。

    这样拆的好处：整条链的分支可以不看 DB 就穷举；单测里塞几个 set 就够了。
    """
    now: dt.datetime
    scheduler_on: bool = True
    dry_run: bool = False
    # target_date: 现在该处理哪个交易日（tradeday.target_date() 算出来的）
    target_date: dt.date | None = None
    # 该交易日已就绪的数据集 key（on_ready 判定用）
    ready_datasets: frozenset[str] = frozenset()
    # 该交易日已成功跑完的 job_key（chain 判定用）
    deps_succeeded: frozenset[str] = frozenset()
    # 该交易日已有 succeeded 记录的 job_key（幂等判定用）—— 与上一个集合物理上
    # 常常相同，但语义不同：一个问"我的前置好了没"，一个问"我自己跑过没"。
    self_succeeded: frozenset[str] = frozenset()
    quality_blocked: bool = False
    # 交易日判定回调。传函数而不是传 set，因为"某一天是不是交易日"在跨年
    # 边界上需要现查日历，调用方不愿意为此每 tick 拉全表。
    trade_day_of: Callable[[dt.date], bool] = lambda d: d.weekday() < 5
    catchup_days: int = 5
    slot_ttl_sec: int = DEFAULT_SLOT_TTL_SEC


def _seed_first_slot(expr: str, now: dt.datetime) -> dt.datetime:
    """
    第一次见到某个作业（或 cron 表达式刚被改过）时，从哪个槽开始判。

    规则：**当天已过的那个槽**，没有就取未来那个。

    两个极端都不对。往后取（`next > now`）意味着 15:30 才把调度器打开的人，
    今天永远不会自动跑 `10 15,16,17` —— 得等到明天，而他会以为开关没生效。
    往前无限取则意味着周一早上打开调度器，它会去补上周五那个槽，
    跨了几天才补的动作不该由"启动"这个动作隐式触发 —— 那是 catch-up 的活，
    得由 `scheduler_catchup_days` 明面上控制。

    所以这里只在**今天**之内回溯；补历史交给 `fill_all` 那个按钮。
    """
    start_of_day = dt.datetime.combine(now.date(), dt.time.min)
    today_past = backfill_slots(expr, start_of_day, now)
    if today_past:
        return today_past[-1]
    nxt = _next_cron_slot(expr, now)
    if nxt is None:
        raise ValueError(f'cron 表达式 {expr!r} 在未来没有任何触发点')
    return nxt


def _day_slot(target: dt.date) -> str:
    """没有 cron 表达式的作业（chain / on_ready），一个交易日只有一个槽。"""
    return 'D:' + target.isoformat()


def decide(job: dict, st: SlotState, env: Env) -> Decision:
    """
    判一个作业在这个 tick 该怎么办。

    `job` 是 `config.job` 的一行（dict，字段名与表一致）。
    """
    job_key = job['job_key']
    now = env.now
    label = job.get('name') or job_key

    # ---- ① 资格 ----------------------------------------------------------
    if not job.get('enabled', True):
        return Decision('skip', '作业已停用（config.job.enabled=false）',
                        slot_consumed=False)
    if not env.scheduler_on:
        return Decision('skip', '调度器总开关未打开（scheduler_enabled=false）')

    # ---- ② 触发槽到期 ----------------------------------------------------
    expr = job.get('cron_expr')
    if expr:
        # 表达式改过 → 旧的 next_fire_at 是按老表达式算的，不能用。
        stale = st.cron_used is not None and st.cron_used != expr
        try:
            if stale or st.next_fire_at is None:
                nxt = _seed_first_slot(expr, now)
            else:
                nxt = st.next_fire_at
        except (ValueError, KeyError) as exc:
            # 表达式写错不能让整个 tick 崩：只判这一个作业 error，原因照实写。
            return Decision('skip', f'cron 表达式无法解析：{exc}')
        catchup_floor = now - dt.timedelta(days=max(env.catchup_days, 0))
        if nxt > now:
            return Decision('wait', f'未到触发时刻（下次 {nxt:%m-%d %H:%M}）',
                            next_fire_at=nxt)
        if nxt < catchup_floor:
            # 调度器停了很久。这个槽已经过期，补它没有意义（后面的判定链会
            # 按"现在"重算目标交易日），直接跳过并把 next_fire_at 拉回当下。
            try:
                ahead = _next_cron_slot(expr, now)
            except (ValueError, KeyError):
                ahead = None
            return Decision('skip',
                            f'槽 {slot_of(nxt)} 已过期（早于补跑窗口 '
                            f'{catchup_floor:%m-%d %H:%M}），跳过并把下次触发拉回当下',
                            slot=slot_of(nxt), next_fire_at=ahead,
                            slot_consumed=True, missed=True)
        slot_dt, slot = nxt, slot_of(nxt)
        try:
            advance_to = _next_cron_slot(expr, slot_dt)
        except (ValueError, KeyError):
            advance_to = None
    else:
        # chain / on_ready 型没有自己的时刻：一个交易日一个槽，
        # 只要当天目标交易日不变就反复重判（wait），跨到下一天才换槽。
        if env.target_date is None:
            return Decision('wait', '目标交易日算不出来（日历与降级估算都不可用）')
        slot_dt = dt.datetime.combine(env.target_date, dt.time(15, 30))
        slot = _day_slot(env.target_date)
        advance_to = None

    # ---- ③ 该槽已消费过 --------------------------------------------------
    if st.last_fire_slot == slot:
        # 槽已经处理完了。这里**不能**返回带原因的 wait —— 那样会把上一个 tick
        # 真正写下的判定（"15:10 触发"、"不是交易日所以跳过"）覆盖成
        # "本次已触发过"，页面最该回答的"为什么没跑"就被自己的心跳刷没了。
        # 所以是 idle：只推进 next_fire_at，note 一个字都不动。
        if (now - slot_dt).total_seconds() > env.slot_ttl_sec and advance_to is not None:
            return Decision('idle', '', slot=slot, next_fire_at=advance_to)
        return Decision('idle', '')

    # ---- ④ 交易日 --------------------------------------------------------
    # only_trade_day=false 的作业（fill_all / sync_ths_block / sync_snapshot /
    # tdx_predownload）周末照样该跑 —— 种子数据里逐条注释过原因：复盘多在周末做。
    if job.get('only_trade_day', True) and not env.trade_day_of(slot_dt.date()):
        return Decision('skip', f'{slot_dt:%m-%d} 不是交易日',
                        slot=slot, next_fire_at=advance_to, slot_consumed=True)

    # ---- ⑤ 依赖 ----------------------------------------------------------
    kind = job.get('trigger_kind') or 'manual'
    if kind == 'chain':
        after = job.get('after_job')
        if not after:
            return Decision('skip', 'trigger_kind=chain 但 after_job 是空的',
                            slot=slot, next_fire_at=advance_to, slot_consumed=True)
        if after not in env.deps_succeeded:
            return Decision('wait', f'等前置作业 {after} 当日跑成功', slot=slot)
    elif kind == 'on_ready':
        need = set(job.get('ready_on') or ())
        if not need:
            return Decision('skip', 'trigger_kind=on_ready 但 ready_on 是空的',
                            slot=slot, next_fire_at=advance_to, slot_consumed=True)
        missing = need - set(env.ready_datasets)
        if missing:
            return Decision('wait', '等数据就绪：' + '、'.join(sorted(missing)),
                            slot=slot)

    # ---- ⑥ 质量门禁 ------------------------------------------------------
    if env.quality_blocked and (job.get('kind') in GATED_KINDS):
        return Decision('skip',
                        f'质量门禁拦截（{env.target_date} 有 blocking 规则未通过），'
                        f'{label} 本日不触发；确认是规则写错了可用 '
                        f'quality_gate_override_date 豁免',
                        slot=slot, next_fire_at=advance_to, slot_consumed=True)

    # ---- ⑦ 幂等 ----------------------------------------------------------
    if job_key in env.self_succeeded:
        return Decision('skip', f'{job_key} 在 {env.target_date} 已有成功记录',
                        slot=slot, next_fire_at=advance_to, slot_consumed=True)

    # ---- ⑧ 入队 ----------------------------------------------------------
    why = f'{slot} 触发'
    if env.dry_run:
        why += '（dry-run：只记录判定结果，未入队）'
    return Decision('fire', why, slot=slot,
                    next_fire_at=advance_to, slot_consumed=True)


def backfill_slots(cron_expr: str | None, since: dt.datetime, until: dt.datetime,
                   limit: int = 50) -> list[dt.datetime]:
    """
    列出 `(since, until]` 之间的所有 cron 触发点。catch-up 用。

    调度器正常在跑时不需要它（每个 tick 只消费一个槽，天然一个一个补上来）；
    它存在的意义是让"停机三天后到底该补几次"这个问题能在页面上算给人看。
    """
    if not cron_expr:
        return []
    out: list[dt.datetime] = []
    cursor = since
    while len(out) < limit:
        nxt = _next_cron_slot(cron_expr, cursor)
        if nxt is None or nxt > until:
            break
        out.append(nxt)
        cursor = nxt
    return out


def job_sort(jobs: Iterable[dict]) -> list[dict]:
    """
    同一 tick 内的处理顺序：ingest 在前、compute 在后，其次按 job_key。

    不是为了正确性（依赖判定本来就走 chain/on_ready），是为了让 `sync_daily`
    这类作业在同一个 tick 里先入队，页面和执行历史看起来更连贯。
    """
    rank = {'ingest': 0, 'quality': 1, 'compute': 2, 'report': 3, 'notify': 4}
    return sorted(jobs, key=lambda j: (rank.get(j.get('kind') or '', 9),
                                       j.get('job_key') or ''))
