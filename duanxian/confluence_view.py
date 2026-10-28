# -*- coding: utf-8 -*-
"""选股汇总（交叉命中榜）的**读数层**。写侧与判据在 `app/compute/confluence.py`，
各榜的选法（表、判据版本、排序、取数上限）只住那一份 `SOURCES`；这里只读落好的行。

排序口径是用户 2026-10-13 选的：**命中榜数优先，其次各榜里最好的名次**。
所以这一层**不交**任何合成总分，只有一个 `hits`（整数）和一个名次分位 ——
把"只在象限第 1 名"和"四个榜都第 120 名"排到一起的那种数会骗人。

NaN/NaT 那道闸直接复用 `quant._num`：读数出口在两家各写一遍迟早漏一种类型，
而漏出去的就是浏览器 `JSON.parse` 炸掉的整页空白（接口还 200）。
"""
from __future__ import annotations

import json

from app.compute import confluence as CF
from . import pg
from .quant import (_cache_caveat, _caveats, _comments, _coverage, _dash, _fail, _full_code,
                    _int, _latest, _num, _one, _rows)

TABLE = 'analytics.confluence_daily'
SOURCE_KEYS = CF.SOURCE_KEYS
SOURCE_LABELS = CF.SOURCE_LABELS


def _split_list(v) -> list:
    return [x for x in str(v or '').split(',') if x]


def _sources_arg(raw: str) -> tuple[list, list]:
    """认得的参与筛选，认不得的点名 —— 与埋伏榜 `types`、倍量榜 `quadrants` 同一条规矩。"""
    picked, rejected = [], []
    for cell in str(raw or '').replace('，', ',').split(','):
        k = cell.strip()
        if not k:
            continue
        if k in SOURCE_KEYS:
            if k not in picked:
                picked.append(k)
        elif k not in rejected:
            rejected.append(k)
    return picked, rejected


def summary(date: str = '', min_hits: int = 1, sources: str = '', limit: int = 300) -> dict:
    """交叉命中榜。`min_hits` / `sources` 都在库里筛，位置在 `order by … limit` **之前**。"""
    d = _dash(date) or _latest(TABLE, spec=CF.SPEC_VERSION) or ''
    if not d or not pg.session_has(TABLE, d):
        return _fail('选股汇总', TABLE, d or None)
    lim = max(1, min(int(limit or 300), 1000))
    picked, rejected = _sources_arg(sources)
    try:
        mh = max(1, min(int(min_hits or 1), len(CF.SOURCES)))
    except (TypeError, ValueError):
        mh = 1

    cond, params = '', []
    if mh > 1:
        cond += ' and c.hits >= %s'
        params.append(mh)
    if picked:
        cond += ' and c.hit_sources && %s::text[]'
        params.append(picked)

    rows = _rows(f"""select c.security_code as code, coalesce(s.name, c.security_code) as name,
                          s.board, c.hits,
                          array_to_string(c.hit_sources, ',') as hit_sources,
                          c.ranks::text as ranks, c.sizes::text as sizes,
                          c.metrics::text as metrics,
                          c.best_source, c.best_rank, c.best_rank_pct,
                          c.source_count, c.sources_absent, c.missing,
                          c.spec_version, c.calculated_at,
                          o.value as oper_value, o.percent_rank as oper_pctile,
                          o.rank_in_market as oper_rank, o.quadrant, o.is_stale as oper_stale,
                          sd.pct_change, sd.close
                     from {TABLE} c
                     left join reference_data.security s using (security_code)
                     left join analytics.score o
                            on o.security_code = c.security_code and o.trade_date = c.trade_date
                           and o.capability_key = 'oper_score'
                     left join market_data.stock_daily sd
                            on sd.security_code = c.security_code and sd.trade_date = c.trade_date
                    where c.trade_date = %s and c.spec_version = %s{cond}
                    order by c.hits desc, c.best_rank_pct asc nulls last, c.security_code
                    limit %s""", [d, CF.SPEC_VERSION] + params + [lim]) or []

    for r in rows:
        for k in ('ranks', 'sizes', 'metrics'):
            r[k] = _json_dict(r.get(k))
        r['hit_sources'] = _split_list(r.get('hit_sources'))
        r['hit_labels'] = [SOURCE_LABELS.get(k, k) for k in r['hit_sources']]
        r['hits'] = _int(r.get('hits'))
        r['best_rank'] = _int(r.get('best_rank'))
        r['best_rank_pct'] = _num(r.get('best_rank_pct'), 2)
        r['best_source_label'] = SOURCE_LABELS.get(r.get('best_source'), r.get('best_source'))
        r['source_count'] = _int(r.get('source_count'))
        r['oper_value'] = _num(r.get('oper_value'), 2)
        r['oper_pctile'] = _num(r.get('oper_pctile'), 2)
        r['oper_rank'] = _int(r.get('oper_rank'))
        r['pct_change'] = _num(r.get('pct_change'), 2)
        r['close'] = _num(r.get('close'), 2)
        r['has_daily'] = r.get('close') is not None

    dist, dist_ok = _hits_distribution(d), True
    if dist is None:
        dist, dist_ok = {}, False
    src = _source_counts(d)

    tot = _one(f"""select count(*)::int n,
                          count(*) filter (where c.hits >= 2)::int repeat,
                          max(c.source_count)::int source_count,
                          max(c.sources_absent) absent
                     from {TABLE} c where c.trade_date = %s and c.spec_version = %s""",
               (d, CF.SPEC_VERSION)) or {}

    caveats = _caveats('coverage') + _cache_caveat(('coverage:confluence_daily',))
    if rejected:
        caveats.append({'code': 'confluence_source_rejected',
                        'text': '交叉源筛选里这些值不是已登记的源：' + '、'.join(rejected)
                                + ' —— 它们没有参与筛选（本轮交的是不筛源的榜）。可用的是 '
                                + '、'.join(f'{s.key}（{s.label}）' for s in CF.SOURCES)})
    if not dist_ok:
        caveats.append({'code': 'confluence_dist_unread',
                        'text': '命中榜数的全场分布没读到（那条聚合失败了），界面按"没读到"显示，'
                                '不拿空分布冒充"没有一只票重复出现"。'})
    absent = _split_list(tot.get('absent'))
    if absent:
        caveats.append({'code': 'confluence_source_absent',
                        'text': '这一场有源没算出数：'
                                + '、'.join(SOURCE_LABELS.get(k, k) for k in absent)
                                + f' —— 当天的「命中几个榜」只数得出剩下的 {tot.get("source_count")} 个源，'
                                  '所以"只命中 1 榜"不等于"判据不认同它"，等那条链补完再比。'})
    stale = [str(r['code'])[:6] for r in rows if r.get('oper_stale')]
    if stale:
        caveats.append({'code': 'oper_stale',
                        'text': f'这些票的趋势评分是沿用旧场的（is_stale）：{"、".join(stale[:8])}'
                                + ('…' if len(stale) > 8 else '')
                                + ' —— 沿用分与新算分不能横比，所以这一格不参与"谁更强"的判断。'})
    if not rows:
        caveats.append({'code': 'confluence_empty',
                        'text': f'当前筛选条件下这一场没有票（当天进过至少一个榜的一共 {_int(tot.get("n")) or 0} 只，'
                                f'其中重复出现的 {_int(tot.get("repeat")) or 0} 只）。'})

    return {'available': True, 'date': d, 'source': 'pg', 'spec_version': CF.SPEC_VERSION,
            'filter': {'min_hits': mh, 'sources': picked, 'sources_rejected': rejected,
                       'rows_returned': len(rows), 'limit': lim},
            'sources': [{'key': s.key, 'label': s.label, 'meaning': s.meaning, 'board': s.board,
                         'limit': s.limit, 'count': (src or {}).get(s.key, {}).get('n'),
                         'size': (src or {}).get(s.key, {}).get('sz')}
                        for s in CF.SOURCES],
            'hits_distribution': dist if dist_ok else None,
            'day_total': _int(tot.get('n')), 'day_repeat_total': _int(tot.get('repeat')),
            'source_count': _int(tot.get('source_count')),
            'source_total': len(CF.SOURCES),      # 分母的满值只住 SOURCES 那一份，页面不写死个数
            'caliber': ('排序：命中榜数（hits）优先，其次各榜里最好的名次'
                        '（名次分位 = 该榜名次 ÷ 该榜这一场的榜长）。没有合成总分 —— '
                        '把"只在象限第 1 名"和"四个榜都第 120 名"排到一起的那种数会骗人。'
                        '各榜彼此不独立（倍量榜的票本来就常排在象限前面），'
                        '所以 hits 多只等于"值得多看一眼"，不等于涨跌概率。'
                        '趋势评分那一格是 analytics.score 的 oper_score 原值与当日分位，不是本页算的。'),
            'definitions': _comments('confluence_daily'),
            'coverage': _coverage(('confluence_daily',)),
            'caveats': caveats, 'rows': rows}


def _hits_distribution(d):
    """全场命中分布（不带任何筛条件）：「至少 2 榜有几只」这种数只能这么问。"""
    try:
        got = {}
        for r in _rows(f"""select c.hits h, count(*)::int n from {TABLE} c
                            where c.trade_date = %s and c.spec_version = %s
                            group by 1 order by 1""", (d, CF.SPEC_VERSION)) or []:
            got[str(_int(r.get('h')))] = _int(r.get('n'))
        return got
    except Exception:      # noqa: BLE001  失败要出声：交给上层标 caveat
        return None


def _source_counts(d):
    """每个源这一场贡献了几只票、该榜榜长多少。缺席的源不会出现在这里面（读侧显示 —）。"""
    try:
        got = {}
        for r in _rows(f"""select k, count(*)::int n, max((c.sizes->>k)::int) sz
                              from {TABLE} c, lateral unnest(c.hit_sources) k
                             where c.trade_date = %s and c.spec_version = %s
                             group by 1""", (d, CF.SPEC_VERSION)) or []:
            got[str(r['k'])] = {'n': _int(r.get('n')), 'sz': _int(r.get('sz'))}
        return got
    except Exception:      # noqa: BLE001  读不到就是读不到，那一格显示 —，不当 0
        return {}


def history(code: str, date: str = '', sessions: int = 250) -> dict:
    """一只票**历史上**被这些榜同时点到过哪些天 —— 用户点名要看的"重复出现"的时间维度。

    只认表里真有的行：这张表只落"至少命中一个榜"的票，所以查不到的日子意思是
    "那天它一个榜都没进"，不是"那天没数据"（那一种由覆盖度那一行说）。
    """
    sec = _full_code(code)
    if not sec:
        return {'available': False, 'reason': '缺少 code 参数'}
    d = _dash(date) or _latest(TABLE, spec=CF.SPEC_VERSION) or ''
    if not d:
        return _fail('交叉命中历史', TABLE, None)
    n = max(1, min(int(sessions or 250), 500))
    rows = _rows(f"""select trade_date::text as day, hits,
                            array_to_string(hit_sources, ',') as hit_sources,
                            best_source, best_rank, best_rank_pct, source_count, sources_absent
                       from {TABLE}
                      where security_code = %s and spec_version = %s and trade_date <= %s
                      order by trade_date desc limit %s""", (sec, CF.SPEC_VERSION, d, n)) or []
    for r in rows:
        r['hit_sources'] = _split_list(r.get('hit_sources'))
        r['hit_labels'] = [SOURCE_LABELS.get(k, k) for k in r['hit_sources']]
        r['hits'] = _int(r.get('hits'))
        r['best_rank'] = _int(r.get('best_rank'))
        r['best_rank_pct'] = _num(r.get('best_rank_pct'), 2)
        r['source_count'] = _int(r.get('source_count'))
        r['best_source_label'] = SOURCE_LABELS.get(r.get('best_source'), r.get('best_source'))
    repeat = [r for r in rows if (r.get('hits') or 0) >= 2]
    absent_days = sorted({r.get('day') for r in rows if _split_list(r.get('sources_absent'))})
    return {'available': True, 'code': sec, 'date': d, 'spec_version': CF.SPEC_VERSION,
            'sessions_read': len(rows), 'rows': rows,
            'repeat_days': [r['day'] for r in repeat],
            'repeat_total': len(repeat),
            'partial_source_days': absent_days[:20],
            'caliber': ('这一列只覆盖本表算过的那些天（判据版本 ' + CF.SPEC_VERSION + '）。'
                        '某天不在这里 = 那天它一个榜都没进；整场没算是另一回事，'
                        '看上面「sessions_read」与那一场的覆盖度。'),
            'caveats': ([{'code': 'confluence_history_partial_sources',
                          'text': f'这 {len(absent_days)} 天里有源没跑完（{absent_days[0]} 起）、'
                                  '那几天的"只命中 1 榜"不能与源齐全的横比。'}]
                        if absent_days else [])}


def _json_dict(v):
    """jsonb 走 pandas 会变成字符串；读不出形状就交 None（不是空 dict 冒充"一个名次都没有"）。"""
    if isinstance(v, dict):
        return v
    if isinstance(v, (bytes, bytearray)):
        v = v.decode('utf-8', 'replace')
    if isinstance(v, str):
        try:
            got = json.loads(v)
        except ValueError:
            return None
        return got if isinstance(got, dict) else None
    return None
