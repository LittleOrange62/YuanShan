# -*- coding: utf-8 -*-
"""盘中事件流/播报留痕的**唯一写入口**（DDL 54）。

三条规矩：
· 幂等：`event_key`/`broadcast_key` 撞了就 `DO NOTHING` —— 重跑同一针、重放同一份历史
  都不会插出第二行（外部那套只有 jsonl 追加，重跑就是重复文本）。
· 缺数写 NULL，`missing` jsonb 点名为什么缺；**不写 0**。
· 写入方只有这里。作业脚本不许自己拼 INSERT。
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Optional

from app.db import engine

from app.intraday.pool_events import KIND_LABELS, KIND_SIDE, SPEC_VERSION


def _no_test_write(what: str) -> None:
    """测试进程里**硬挡真写库**（与 `_spawn_stage` / `_post_json` / `run_powershell` 同一条纪律）。

    11-03 抓到的现场：`tests/test_noon_review.py` 有两条用例真跑了 `market_stats()`，
    而它内部会写 `analytics.intraday_broadcast` —— 于是当天多出 22 行"东财/腾讯取数失败"
    的**假留痕**，界面那一格与 15:40 的看护都在念这些不存在的事，而且没人会怀疑到测试头上。
    读侧不挡（读不脏数据）。
    """
    if os.environ.get('PYTEST_CURRENT_TEST'):
        raise RuntimeError(f'pytest 进程里不许真写 analytics 表（{what}）—— 要打桩 store 这一层')

_EVT = """
INSERT INTO analytics.intraday_pool_event (
    trade_date, captured_at, event_key, spec_version,
    side, kind, kind_label, security_code, security_name, pct, price,
    board_count, open_count, first_seal_time, seal_fund_yuan, industry,
    pool_zt_n, pool_zb_n, pool_dt_n, round_seq, is_baseline,
    basis, source, missing, calculated_at)
VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s,
        'source_pool_diff','eastmoney_push2ex', %s::jsonb, now())
ON CONFLICT (trade_date, captured_at, event_key, spec_version) DO NOTHING
"""

_BCAST = """
INSERT INTO analytics.intraday_broadcast (
    trade_date, broadcast_key, spec_version, task, what, at_ts, ok,
    channel_key, http_status, delivered, err, body, counts, source_detail, calculated_at)
VALUES (%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s::jsonb, %s::jsonb, now())
ON CONFLICT (trade_date, broadcast_key, spec_version)
DO UPDATE SET ok = EXCLUDED.ok, delivered = EXCLUDED.delivered,
              err = EXCLUDED.err, channel_key = EXCLUDED.channel_key
"""


def event_key(captured_at: dt.datetime, kind: str, code: str) -> str:
    """幂等键。同一针（同一 `captured_at`）同一只票同一类事件只有一行。"""
    return f'{captured_at:%Y%m%d%H%M%S}|{kind}|{code}'


def write_events(captured_at: dt.datetime, events: list[dict], *,
                 pool_n: dict[str, Optional[int]], round_seq: Optional[int] = None,
                 is_baseline: bool = False, trade_date: Optional[dt.date] = None) -> int:
    """落一整针的事件。返回**新插入**的行数（撞键的不算，那是重放）。"""
    _no_test_write('intraday_pool_event')
    day = trade_date or captured_at.date()
    rows = []
    for e in events:
        kind = e['kind']
        rows.append((
            day, captured_at, event_key(captured_at, kind, e['code']), SPEC_VERSION,
            KIND_SIDE.get(kind, 'zt'), kind, KIND_LABELS.get(kind, kind),
            e['code'], e.get('name'), e.get('pct'), e.get('price'),
            e.get('board_count'), e.get('open_count'), e.get('first_seal_time'),
            e.get('seal_fund_yuan'), e.get('industry'),
            pool_n.get('zt'), pool_n.get('zb'), pool_n.get('dt'),
            round_seq, is_baseline,
            json.dumps(e.get('missing') or {}, ensure_ascii=False)))
    if not rows:
        return 0
    return engine.executemany(_EVT, rows, role='etl')


def write_broadcast(captured_at: dt.datetime, *, task: str, what: str,
                    body: str = '', ok: Optional[bool] = None,
                    delivered: Optional[bool] = None, err: Optional[str] = None,
                    channel_key: Optional[str] = None, http_status: Optional[int] = None,
                    counts: Optional[dict[str, Any]] = None,
                    source_detail: Optional[dict[str, Any]] = None,
                    trade_date: Optional[dt.date] = None) -> int:
    """一条播报/一次失败留痕。`delivered` 必须是**业务码**判出来的，不是 HTTP 200。"""
    _no_test_write('intraday_broadcast')
    day = trade_date or captured_at.date()
    key = f'{task}|{captured_at:%Y%m%d%H%M%S}|{what}'
    return engine.execute(_BCAST, (
        day, key, SPEC_VERSION, task, what, captured_at, ok,
        channel_key, http_status, delivered, err, body or None,
        json.dumps(counts or {}, ensure_ascii=False),
        json.dumps(source_detail or {}, ensure_ascii=False)), role='etl')


_CONCEPT = """
INSERT INTO analytics.intraday_concept_count (
    trade_date, asof_ts, bucket, sector_name, spec_version,
    sector_code, multi_code, hit_n, rank, share_pct, tier, top_board_count, reps,
    sector_pct, sector_pct_asof, unmapped_n, calculated_at)
VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s, now())
ON CONFLICT (trade_date, asof_ts, bucket, sector_name, spec_version)
DO UPDATE SET hit_n = EXCLUDED.hit_n, rank = EXCLUDED.rank,
              sector_pct = EXCLUDED.sector_pct, sector_pct_asof = EXCLUDED.sector_pct_asof,
              unmapped_n = EXCLUDED.unmapped_n, calculated_at = now()
"""

_AUCTION = """
INSERT INTO analytics.auction_board (
    trade_date, board, rank, spec_version,
    security_code, security_name, pct, amount_yuan, board_count, seal_fund_yuan,
    first_seal_time, industry, listed_n, truncated_n, source, unavailable, reason,
    captured_at, calculated_at)
VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s, now())
ON CONFLICT (trade_date, board, rank, spec_version)
DO UPDATE SET security_code = EXCLUDED.security_code, pct = EXCLUDED.pct,
              amount_yuan = EXCLUDED.amount_yuan, source = EXCLUDED.source
"""


def upsert_auction_rows(rows: list[tuple]) -> int:
    """竞价双榜 + 一字块。元组顺序同 `_AUCTION`；`unavailable=True` 的那一侧
    也要落一行（`security_code=None`、rank=0、reason 写明没取到），
    否则"跌幅榜挂了"这件事到库里就变成"那天没有跌幅榜"，两种情况事后分不出来。"""
    _no_test_write('auction_board')
    return engine.executemany(_AUCTION, rows, role='etl')


_NOON = """
INSERT INTO analytics.noon_review (
    trade_date, spec_version,
    limit_up_n, limit_down_n, unseal_n, unseal_rate_pct, max_board_text, max_board_count,
    board2_codes, idx_sh_pct, idx_sz_pct, idx_bj_pct, up_count, down_count,
    premium_n, premium_pos_n, premium_pos_rate_pct, premium_avg_pct, mood_label, mood_tags,
    section_mood, section_cluster, section_watch, body,
    pool_source, index_source, ladder_basis, delivered, missing, calculated_at)
VALUES (%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,
        %s,%s,%s,%s, %s::jsonb, now())
ON CONFLICT (trade_date, spec_version) DO UPDATE SET body = EXCLUDED.body,
    delivered = EXCLUDED.delivered, calculated_at = now()
"""


def upsert_noon(row: tuple) -> int:
    """12:10 午间快照，一场一行。"""
    _no_test_write('noon_review')
    return engine.execute(_NOON, row, role='etl')


#: `_NOON` 的插入列序（不含 `now()` 填的 `calculated_at`）。作业侧按**这个名字序**交值，
#: 于是"插错一格"这种事不会靠数位置来防：列名改了而常量没改，导入当场就红。
NOON_COLUMNS = ('trade_date', 'spec_version',
                'limit_up_n', 'limit_down_n', 'unseal_n', 'unseal_rate_pct',
                'max_board_text', 'max_board_count', 'board2_codes',
                'idx_sh_pct', 'idx_sz_pct', 'idx_bj_pct', 'up_count', 'down_count',
                'premium_n', 'premium_pos_n', 'premium_pos_rate_pct', 'premium_avg_pct',
                'mood_label', 'mood_tags', 'section_mood', 'section_cluster', 'section_watch',
                'body', 'pool_source', 'index_source', 'ladder_basis', 'delivered', 'missing')


def noon_row(parts: dict) -> tuple:
    """把命名的读数按 `_NOON` 的列序排成一行；少一个键/多一个键都当场说清楚。"""
    missing_keys = [c for c in NOON_COLUMNS if c not in parts]
    if missing_keys:
        raise KeyError(f"午间快照少这些格：{'、'.join(missing_keys)}")
    extra_keys = [k for k in parts if k not in NOON_COLUMNS]
    if extra_keys:
        raise KeyError(f"午间快照有不知道往哪放的格：{'、'.join(extra_keys)}")
    return tuple(parts[c] for c in NOON_COLUMNS)


def _noon_sql_columns() -> tuple[str, ...]:
    head = _NOON.split('(', 1)[1].split(')', 1)[0]
    return tuple(c.strip() for c in head.replace('\n', ' ').split(',') if c.strip())


assert _noon_sql_columns() == NOON_COLUMNS + ('calculated_at',), \
    'analytics.noon_review 的插入列序与 NOON_COLUMNS 不一致（改了 DDL 就要一起改这里）'


#: 盘中 14:40 交易体系榜的**取数落库**侧（DDL 60）。发送侧只读这张表，一次源站都不碰。
_SYSENTRY = """
INSERT INTO analytics.intraday_sysentry (
    trade_date, captured_at, spec_version, security_code, list_kind,
    security_name, rank, close, pre_close, pct,
    gap_date, gap_pct, gap_unfilled, pullback_max_vol_ratio, pullback_down_cnt,
    stop_ref_hfq, adjustment_factor, board, quote_source, quote_day, calculated_at)
VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s, now())
ON CONFLICT (trade_date, captured_at, security_code, spec_version)
DO UPDATE SET list_kind = EXCLUDED.list_kind, rank = EXCLUDED.rank,
              security_name = EXCLUDED.security_name, close = EXCLUDED.close,
              pre_close = EXCLUDED.pre_close, pct = EXCLUDED.pct,
              gap_date = EXCLUDED.gap_date, gap_pct = EXCLUDED.gap_pct,
              gap_unfilled = EXCLUDED.gap_unfilled,
              pullback_max_vol_ratio = EXCLUDED.pullback_max_vol_ratio,
              pullback_down_cnt = EXCLUDED.pullback_down_cnt,
              stop_ref_hfq = EXCLUDED.stop_ref_hfq, adjustment_factor = EXCLUDED.adjustment_factor,
              board = EXCLUDED.board, quote_source = EXCLUDED.quote_source,
              quote_day = EXCLUDED.quote_day, calculated_at = now()
"""

#: `_SYSENTRY` 的插入列序（不含 `now()` 填的 `calculated_at`）。`app/intraday/sysentry.py` 的
#: `table_rows()` 按这个名字序交值 —— 加一格时两边一起改，漏一边导入当场红（同 noon 那道闸）。
SYSENTRY_COLUMNS = ('trade_date', 'captured_at', 'spec_version', 'security_code', 'list_kind',
                    'security_name', 'rank', 'close', 'pre_close', 'pct',
                    'gap_date', 'gap_pct', 'gap_unfilled', 'pullback_max_vol_ratio',
                    'pullback_down_cnt', 'stop_ref_hfq', 'adjustment_factor', 'board',
                    'quote_source', 'quote_day')


def _sysentry_sql_columns() -> tuple[str, ...]:
    head = _SYSENTRY.split('(', 1)[1].split(')', 1)[0]
    return tuple(c.strip() for c in head.replace('\n', ' ').split(',') if c.strip())


assert _sysentry_sql_columns() == SYSENTRY_COLUMNS + ('calculated_at',), \
    'analytics.intraday_sysentry 的插入列序与 SYSENTRY_COLUMNS 不一致'


def upsert_sysentry_rows(rows: list[tuple]) -> int:
    """一场榜的逐票读数落库（`entry` 与 `watch` 都落；观察池原来只报个数，事后无从回查）。"""
    _no_test_write('intraday_sysentry')
    return engine.executemany(_SYSENTRY, rows, role='etl')


def sysentry_latest_run(day: dt.date) -> Optional[dict]:
    """今天最后一次成功落库的那一针（`(captured_at, quote_source, quote_day)`）。

    读不到 ⇒ None，调用方要说"库里今天还没有这一场的数"，**不能**当成"今天 0 只上榜"。
    注意 PG 的空集聚合仍会回一行（`max` 是 NULL），所以这里判的是 `captured_at` 那一格，
    不是"有没有回行"—— 把空集当读数是最容易蒙过调用方的一种假绿。
    """
    row = engine.query_one(
        'SELECT max(captured_at) AS captured_at,'
        "       (array_agg(quote_source order by captured_at desc))[1] AS quote_source,"
        "       (array_agg(quote_day order by captured_at desc))[1] AS quote_day"
        '  FROM analytics.intraday_sysentry WHERE trade_date = %s', (day,), role='reader')
    if not row or not row.get('captured_at'):
        return None
    return row


def sysentry_rows(day: dt.date, captured_at: Any) -> dict[str, list[dict]]:
    """那一针的全部行，按 `list_kind` 分成 `entry`/`watch` 两组（组内按 rank 升序）。"""
    rows = engine.query(
        'SELECT * FROM analytics.intraday_sysentry'
        ' WHERE trade_date = %s AND captured_at = %s ORDER BY list_kind, rank NULLS LAST',
        (day, captured_at), role='reader') or []
    out: dict[str, list[dict]] = {'entry': [], 'watch': []}
    for r in rows:
        out.setdefault(r['list_kind'], []).append(r)
    return out


def sysentry_run_at(day: dt.date, want: dt.datetime, *, tol_s: int = 120) -> Optional[dict]:
    """按墙钟找今天**最接近** `want` 的那一针（±`tol_s` 秒内），返回 `(captured_at, quote_source, quote_day, drift_s)`。

    手工补发时人记的是"14:39"，库里存的是带微秒的取数时刻（实测 `18:01:36.991056`）——
    拿整秒去等值匹配会匹配不到，然后**正确地把那一针说成不存在**，那种"看着像 bug"的坑必须在读侧堵掉。
    超出容差就真的返回 None（不拿十分钟外的另一针冒充）。
    """
    rows = engine.query(
        "SELECT captured_at, min(quote_source) AS quote_source, min(quote_day) AS quote_day,"
        "       min(abs(extract(epoch FROM captured_at - %s))) AS drift_s"
        "  FROM analytics.intraday_sysentry"
        " WHERE trade_date = %s AND captured_at BETWEEN %s AND %s"
        " GROUP BY captured_at ORDER BY drift_s LIMIT 1",
        (want, day, want - dt.timedelta(seconds=tol_s), want + dt.timedelta(seconds=tol_s)),
        role='reader') or []
    return rows[0] if rows else None


def sysentry_fill_summary(day: dt.date, captured_at: Any) -> Optional[dict]:
    """A 侧那一针的摘要（候选数/未判/除权/档价不一致…）——存在播报留痕表的 `counts` 里。

    与 `sysentry_rows` 同一条 `captured_at` 对齐：摘要与行不是同一针的话，
    榜尾那句"未判 3 只"念的就是另一场的事。读不到返回 None（渲染侧要显式说缺数）。
    """
    row = engine.query_one(
        'SELECT counts FROM analytics.intraday_broadcast'
        " WHERE trade_date = %s AND task = 'sysentry_fill' AND what = 'digest'"
        '   AND at_ts = %s AND spec_version = %s', (day, captured_at, SPEC_VERSION), role='reader')
    if not row:
        return None
    c = row.get('counts')
    return json.loads(c) if isinstance(c, str) else (c or {})


def upsert_concept_counts(rows: list[tuple]) -> int:
    """
    批量落概念计数（竞价／热度／午间共用这一张表、这一个入口）。

    元组顺序与 `_CONCEPT` 一致：`(trade_date, asof_ts, bucket, sector_name, spec_version,
    sector_code, multi_code, hit_n, rank, share_pct, tier, top_board_count, reps,
    sector_pct, sector_pct_asof, unmapped_n)`。`sector_pct` **必须是百分数**
    （换算在 `sources.sector_pcts` 一处做完，库里 `sector_daily.pct_change` 是小数）。
    """
    _no_test_write('intraday_concept_count')
    return engine.executemany(_CONCEPT, rows, role='etl')


def state_summary(day: dt.date) -> dict:
    """当日已落库的针数/事件数/播报数 —— 作业自己回读，别拿"我写了"当"库里有了"。"""
    return engine.query_one(
        "SELECT count(*) AS events, count(DISTINCT captured_at) AS rounds,"
        "       min(captured_at) AS first_at, max(captured_at) AS last_at"
        "  FROM analytics.intraday_pool_event"
        " WHERE trade_date = %s AND spec_version = %s", (day, SPEC_VERSION), role='reader') or {}


def broadcast_ledger(day: dt.date) -> dict[str, dict]:
    """那一天每一路播报的真读数：打了几针、真发出去几条、几条没成、首末时间。

    界面上「配置开着」与「群里真收到过」是两件事，这一格回答后者。
    查不到（库挂、表不存在）要**抛**，让接口说"判断不了"；兜成空表就变成
    "今天一条都没发"，那是把观测层的故障说成业务事实。
    """
    rows = engine.query(
        "SELECT task, what, count(*) AS n, min(at_ts) AS first_at, max(at_ts) AS last_at,"
        "       bool_and(ok) AS all_ok, max(err) AS last_err"
        "  FROM analytics.intraday_broadcast"
        " WHERE trade_date = %s AND spec_version = %s"
        " GROUP BY task, what", (day, SPEC_VERSION), role='reader') or []
    return fold_ledger(rows)


def fold_ledger(rows: list[dict]) -> dict[str, dict]:
    """把 `task × what` 的聚合行折成每路一格的读数（纯函数，测试直接喂假行）。

    分类只有这一处：`sent` 算真发出去几条、`digest/heat/delta/auction/noon/mirror/sysentry*` 算打了几针、
    `skipped*` 属正常（非时段/休市）、其余一律是缺数与降级 —— 把它们并进 rounds 或 sent
    就是"这一轮没做成"被说成"这一轮发过了"。
    """
    out: dict[str, dict] = {}
    for r in rows:
        bucket = out.setdefault(r['task'], {'rounds': 0, 'sent': 0, 'failed': 0,
                                            'skipped': 0, 'notes': [], 'last_err': None,
                                            'first_at': None, 'last_at': None})
        what = r['what']
        if what in ('digest', 'heat', 'delta', 'auction', 'noon', 'mirror',
                    'sysentry', 'sysentry_fill', 'daily_review'):
            bucket['rounds'] += int(r['n'])
        elif what == 'sent':
            bucket['sent'] += int(r['n'])
            if r['all_ok'] is False:
                bucket['failed'] += int(r['n'])
                bucket['last_err'] = bucket['last_err'] or str(r['last_err'] or '')[:200]
        elif str(what).startswith('skipped'):
            bucket['skipped'] += int(r['n'])
        else:
            bucket['notes'].append({'what': what, 'n': int(r['n']),
                                    'at': str(r['last_at'] or '')[:19],
                                    'err': str(r['last_err'] or '')[:160]})
        for edge, stamp in (('first_at', r['first_at']), ('last_at', r['last_at'])):
            if not stamp:
                continue
            cur = bucket[edge]
            if cur is None or (stamp < cur) == (edge == 'first_at'):
                bucket[edge] = stamp
    return out
