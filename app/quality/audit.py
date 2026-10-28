# -*- coding: utf-8 -*-
"""
质量审计执行器 —— 把 `config.quality_rule` 登记的那些规则**真的按交易日跑一遍**（条数以表为准，这里不抄数）。

为什么要有这个模块（2026-08-30）：规则表里登记着规则（当时 24 条，2026-10-18 现查 30 条）、页面上有「校验规则
登记表」和「最近校验结果」两张卡，但 `operations.quality_result` 里只有 **2 行**、
`trade_date` 全是 NULL —— 那是迁移对账和备份验证顺手写的。也就是说
**质量审计从来没有按交易日跑过一次**，页面上那个「最近一次 2026-08-26 17:54」
是迁移那天的时间戳，不是"08-26 的数据体检过了"。规则声明了却没人执行，
比没有规则更危险：看板上一片绿，实际什么都没查。

判据来源全部在表里，这里只负责执行：

    check_kind = 'sql_bool'   check_body 是一条返回单个 boolean 的 SQL，
                              `:d` 是被审计的交易日占位符
    check_kind = 'builtin'    check_body 是**库层约束/触发器的名字**。这类规则
                              由 PostgreSQL 在写入时强制，审计要做的是确认
                              "那个约束还在、还挂在所有分区上" —— 约束被谁
                              DROP 掉了，是最典型的"看板全绿但防线没了"。

**空缺口不是失败**：某天没有任何行情（还没采）就跳过，不写"未通过"，
否则周末点一下页面会凭空多出一堆红。
"""
from __future__ import annotations

import datetime as dt
import re
import time
from collections.abc import Callable

from app.db import engine
from app.db.repo import ops as ops_repo
from app.orchestrator import tradeday

#: `check_body` 里的交易日占位符。见 `_prepare()` 里为什么不能用裸 replace。
_D_PLACEHOLDER = re.compile(r'(?<!:):d(?![0-9A-Za-z_])')

#: 缺口自动回补的默认上限（交易日）。`operations.quality_result` 里一天都没有时，
#: 缺口按定义是"有行情的所有交易日"（2,500 多天）—— 那不是追平，是补史。
#: 一次点击最多补这么多天，更早的要显式 `--recent N` / `--date`。
DEFAULT_BACK = 10

#: 单条规则的执行上限。跨日规则里有 percentile / 全表 join，
#: 一条卡住不能把整次审计拖死 —— 超时按"未通过 + 超时原因"记，继续下一条。
RULE_TIMEOUT_MS = 60_000


def rules(*, only: list[str] | None = None,
          include_disabled: bool = False) -> list[dict]:
    """规则清单。**顺序即执行顺序**：先便宜的行级、再表级、最后跨日跨表。"""
    rows = engine.query(
        """
        SELECT rule_key, name, scope, severity, target, check_kind, check_body,
               threshold, blocking, enabled, description
          FROM config.quality_rule
         WHERE (%s OR enabled)
         ORDER BY CASE scope WHEN 'row' THEN 0 WHEN 'table' THEN 1
                             WHEN 'cross_day' THEN 2 WHEN 'cross_table' THEN 3
                             ELSE 4 END, rule_key
        """, (include_disabled,))
    if only:
        keep = set(only)
        rows = [r for r in rows if r['rule_key'] in keep]
    return rows


def _prepare(body: str) -> str:
    """
    `check_body` → 可参数化执行的 SQL。

    两处都是踩过的坑：

    - 先把字面量 `%` 转义成 `%%`（psycopg 会把 `%` 当占位符起头），**再**把 `:d`
      换成 `%(d)s`。反过来的话刚插进去的占位符会被自己转义掉，报
      "argument formats can't be mixed"。
    - `:d` 只在**独立出现**时才是占位符。裸 `replace(':d', ...)` 会把
      `(m.dt AT TIME ZONE 'Asia/Shanghai')::date` 里的 `::d` 也换掉，变成
      `'...'):%(d)sate` —— `minute_daily_consistent` 那条规则第一版就是这么废的。
      所以用负向前后断言：前面不能是 `:`，后面不能接标识符字符。
    """
    return _D_PLACEHOLDER.sub('%(d)s', body.replace('%', '%%'))


def _run_sql_bool(rule: dict, d: dt.date) -> tuple[bool, dict]:
    sql = _prepare(rule['check_body'])
    t0 = time.perf_counter()
    with engine.cursor(role='reader') as cur:
        # `SET LOCAL` 不接受参数（`SET LOCAL x = $1` 直接语法错误），只能拼字面量。
        # 拼的是本模块的 int 常量，不是外部输入。
        cur.execute(f'SET LOCAL statement_timeout = {int(RULE_TIMEOUT_MS)}')
        cur.execute(sql, {'d': d})
        row = cur.fetchone()
    ms = int((time.perf_counter() - t0) * 1000)
    val = None if row is None else list(row.values())[0] if isinstance(row, dict) \
        else row[0]
    # SQL 返回 NULL 不等于通过：`avg20` 为空（历史不够长）会让整个表达式变 NULL。
    # 记成未通过并说清是 NULL，比静默算通过安全。
    passed = val is True
    actual = {'value': val, 'ms': ms}
    if val is None:
        actual['note'] = '判据返回 NULL（样本不足或除零），按未通过记'
    return passed, actual


def _run_builtin(rule: dict, d: dt.date) -> tuple[bool, dict]:
    """
    库层约束/触发器还在不在。`d` 用不上，但签名与 sql_bool 保持一致。

    `market_data.stock_daily` 是按年分区的，约束在**每个分区上各有一份**，所以这里报
    个数：数量突然变少通常是新分区建出来时漏了约束（新的一年第一天写进去
    才会暴露，而那时已经写脏了）。
    """
    name = (rule['check_body'] or '').strip()
    n_con = engine.scalar(
        'SELECT count(*) FROM pg_constraint WHERE conname = %s', (name,)) or 0
    n_trg = engine.scalar(
        'SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname = %s',
        (name,)) or 0
    n = int(n_con) + int(n_trg)
    kind = '约束' if n_con else ('触发器' if n_trg else '不存在')
    return n > 0, {'object': name, 'kind': kind, 'copies': n}


def has_data(d: dt.date) -> bool:
    """这一天有没有个股日线。没有就没什么可审计的。"""
    return bool(engine.scalar(
        'SELECT 1 FROM market_data.stock_daily WHERE trade_date = %s LIMIT 1', (d,)))


def audit_day(d: dt.date, *, only: list[str] | None = None, save: bool = True,
              echo: Callable[[str], None] | None = None) -> dict:
    """
    审计一个交易日。返回 `{date, total, failed, blocking, errors, results}`。

    `save=False` 时只算不写库 —— `--dry-run` 和测试用。
    """
    say = echo or (lambda _s: None)
    rs = rules(only=only)
    out: list[dict] = []
    failed = blocking = errored = 0
    for r in rs:
        try:
            if r['check_kind'] == 'sql_bool':
                passed, actual = _run_sql_bool(r, d)
            elif r['check_kind'] == 'builtin':
                passed, actual = _run_builtin(r, d)
            else:
                passed, actual = False, {'note': f"不认识的 check_kind：{r['check_kind']}"}
        except Exception as exc:  # noqa: BLE001
            # 规则本身跑不起来（SQL 写错、表被改名）**也要留痕**。静默跳过的
            # 结果是页面上少一行、没人发现这条防线已经哑了。
            passed, actual = False, {'error': f'{type(exc).__name__}: {exc}'[:500]}
            errored += 1
        if not passed:
            failed += 1
            if r['blocking']:
                blocking += 1
        if save:
            ops_repo.save_quality_result(
                d, r['rule_key'], r['target'], r['severity'], passed,
                actual=actual, expected=r['threshold'])
        out.append({**r, 'passed': passed, 'actual': actual})
        mark = '✓' if passed else '×'
        say(f"    {mark} {r['rule_key']:<24} "
            f"{'' if passed else _why(actual)}")
    return {'date': d, 'total': len(rs), 'failed': failed,
            'blocking': blocking, 'errors': errored, 'results': out}


def _why(actual: dict) -> str:
    if actual.get('error'):
        return actual['error'][:120]
    if actual.get('note'):
        return actual['note']
    if actual.get('kind') == '不存在':
        return f"库里找不到 {actual.get('object')}"
    return f"判据为 {actual.get('value')}"


def audited_max() -> dt.date | None:
    """已经审计到哪一天。`trade_date IS NULL` 的那些（迁移对账）不算。"""
    return engine.scalar(
        'SELECT max(trade_date) FROM operations.quality_result '
        ' WHERE trade_date IS NOT NULL')


def missing_days(target: dt.date, *, back: int = DEFAULT_BACK) -> list[dict]:
    """
    该补哪几天。返回 `[{date, has_data}]`，按日期升序。

    定义：`reference_data.trade_calendar` 里 `is_open` 且 ≤ 目标日、且 `operations.quality_result`
    里没有这一天的交易日。上界是目标日（不审计还没收盘的今天），下界取
    `max(已审计) + 1` 与"目标日往前 back 个交易日"之中**较晚**的那个 ——
    一天都没审计过时，缺口不会变成 2,500 天。
    """
    rows = engine.query(
        """
        WITH span AS (
            SELECT calendar_date FROM reference_data.trade_calendar
             WHERE is_open AND calendar_date <= %(target)s
             ORDER BY calendar_date DESC LIMIT %(back)s
        )
        SELECT c.calendar_date AS date,
               EXISTS (SELECT 1 FROM market_data.stock_daily s
                        WHERE s.trade_date = c.calendar_date) AS has_data
          FROM reference_data.trade_calendar c
         WHERE c.is_open AND c.calendar_date <= %(target)s
           AND c.calendar_date >= coalesce(
                 greatest((SELECT min(calendar_date) FROM span),
                          (SELECT max(trade_date) + 1 FROM operations.quality_result
                            WHERE trade_date IS NOT NULL)),
                 (SELECT min(calendar_date) FROM span))
           AND NOT EXISTS (SELECT 1 FROM operations.quality_result q
                            WHERE q.trade_date = c.calendar_date)
         ORDER BY c.calendar_date
        """, {'target': target, 'back': max(int(back), 1)})
    return rows


def gap(now: dt.datetime | None = None, *, back: int = DEFAULT_BACK) -> dict:
    """
    页面和脚本共用的"现在差几天"。**只读，不写库、不联网**。

    `now` 默认取**北京时间**（`app.config.TIMEZONE`），不是机器本地时区 ——
    判据是交易日，交易日是按上海时间算的。
    """
    now = now or tradeday.now_bj()
    target, why = tradeday.target_date(now)
    if target is None:
        return {'now': now, 'target': None, 'why': why, 'audited_max': None,
                'missing': [], 'runnable': [], 'no_data': []}
    miss = missing_days(target, back=back)
    return {
        'now': now, 'target': target, 'why': why,
        'audited_max': audited_max(),
        'missing': [r['date'] for r in miss],
        'runnable': [r['date'] for r in miss if r['has_data']],
        'no_data': [r['date'] for r in miss if not r['has_data']],
        'back': back,
    }


def catch_up(*, back: int = DEFAULT_BACK, now: dt.datetime | None = None,
             save: bool = True,
             echo: Callable[[str], None] | None = None) -> dict:
    """
    把缺的交易日**一天一天**补上（差 2 天就跑 2 次，各写自己那一天的结果）。

    没有行情的日子跳过并如实报出来 —— 那是"数据还没采"，不是"质量不合格"，
    两件事混在一起会让人去修一个不存在的质量问题。
    """
    say = echo or (lambda _s: None)
    g = gap(now, back=back)
    if g['target'] is None:
        return {**g, 'ran': [], 'summary': []}
    ran, summary = [], []
    for d in g['runnable']:
        say(f'  === 审计 {d} ===')
        rep = audit_day(d, save=save, echo=echo)
        ran.append(d)
        summary.append({k: rep[k] for k in ('date', 'total', 'failed',
                                            'blocking', 'errors')})
    return {**g, 'ran': ran, 'summary': summary}
