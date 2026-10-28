# -*- coding: utf-8 -*-
"""异动筛查的**读数层**。判据与阈值只住 `app/compute/intraday_spike.py` 那一份；
这里只读落好的行，并把「之后的走势」现算出来。

两件事在这里是刻意的，别改回去：

① **次日/3 日/5 日不入表**（写侧 docstring 里那条）。它们在库里用 `lead` + 复利
   `exp(sum(ln(1+pct/100)))` 现算 —— 拿 close 自己相除会在除权日把跌算成涨；
   用 `lag` 会把「前一天」当成「次日」（这一条我实测错过一次，所有"次日"读数都反了）。

② **每个统计档都必须与同期基线并排交出去**。2026-09-22~10-12 那 9 场的基线是
   次日均值 −0.56%、上涨 37.9%，而 2026-08-14~10-13 两个月的基线是 −0.06%、46.0% ——
   同一个"次日上涨 35%"在前一个窗口里是"跟基线一样"、在后一个里是"明显差"。
   只交档内数字不交基线，屏幕就会把"没优势"读成"危险"，或反过来。

缺席分三档（与档案层同一套词）：库不通 / 这场没算（回算没到）/ 算了但当天没分钟线。
"""
from __future__ import annotations

import datetime as dt
import json

from app.compute import intraday_spike as SP
from . import pg
from .quant import (_cache_caveat, _caveats, _dash, _fail, _int, _latest, _num, _one, _rows)

TABLE = 'analytics.intraday_spike'
TAG_KEYS = tuple(SP.TAG_LABELS)
TAG_LABELS = dict(SP.TAG_LABELS)

# 同期基线的读数口径（全部票·日，不筛冲高）
_BASE_COLS = """select count(*)::int n,
       round(avg(n1)::numeric, 3) avg_next,
       round((percentile_cont(0.5) within group (order by n1))::numeric, 3) med_next,
       round(100.0 * count(*) filter (where n1 > 0) / nullif(count(*) filter (where n1 is not null), 0), 1) win_next,
       round((percentile_cont(0.1) within group (order by n1))::numeric, 2) p10_next,
       round((percentile_cont(0.9) within group (order by n1))::numeric, 2) p90_next,
       round(((exp(avg(l5)) - 1) * 100)::numeric, 3) avg_next5,
       round(100.0 * count(*) filter (where l5 > 0) / nullif(count(*) filter (where l5 is not null), 0), 1) win_next5"""

# 前瞻收益的公共片段：`f` 里有全市场每一票·日的次日与后 3/5 日复利
_FWD = """with f as (select security_code, trade_date,
                    lead(pct_change) over w n1,
                    sum(ln(1 + pct_change/100.0)) over (partition by security_code
                        order by trade_date rows between 1 following and 3 following) l3,
                    sum(ln(1 + pct_change/100.0)) over (partition by security_code
                        order by trade_date rows between 1 following and 5 following) l5
               from market_data.stock_daily
              where trade_date >= %s and pre_close > 0 and close > 0 and pct_change is not null
              window w as (partition by security_code order by trade_date))"""


def _tags_arg(raw: str) -> tuple[list, list]:
    """认得的标签参与筛选，认不得的点名 —— 与交叉源、埋伏类型同一条规矩。"""
    picked, rejected = [], []
    for cell in str(raw or '').replace('，', ',').split(','):
        k = cell.strip()
        if not k:
            continue
        if k in TAG_KEYS:
            if k not in picked:
                picked.append(k)
        elif k not in rejected:
            rejected.append(k)
    return picked, rejected


def _labels_of(tags: list) -> list:
    """标签码 → 中文标签。调用方给的是**已经拆好的列表**（`board()` 里上一行刚拆过）。

    这里原来写的是 `str(tags).split(',')`：喂一个 Python 列表进去会得到
    `"['slow', 'touched']"` 按逗号拆出来的四段带方括号和引号的字符串 —— JSON 完全合法、
    接口 200，屏幕上那一格就是一坨 `['slow' 'touched']`（2026-10-13 浏览器实测抓到）。
    """
    return [TAG_LABELS.get(t, t) for t in (tags or []) if t]


def board(date: str, tags: str = '', min_hi: float | None = None,
          limit: int = 400) -> dict:
    """某一场的异动名单。**排序就是用户点名的时间顺序**：日内高点出现的时刻升序。

    筛选一律拼在 `order by … limit` 之前（前端筛只能筛到交出去的那几百条，
    "这一档还剩几只"会被读成全场只有这么多）。
    """
    d = _dash(date) or _latest(TABLE, spec=SP.SPEC_VERSION) or ''
    if not d or not pg.session_has(TABLE, d):
        return _fail('异动筛查', TABLE, d or None)
    lim = max(1, min(int(limit or 400), 1000))
    picked, rejected = _tags_arg(tags)
    cond, params = '', []
    if picked:
        cond += ' and s.spike_tags && %s::text[]'
        params.append(picked)
    if min_hi is not None:
        cond += ' and s.hi_pct >= %s'
        params.append(float(min_hi))

    sql = _FWD + """
        select s.security_code as code, coalesce(sec.name, s.security_code) as name, sec.board,
               s.hi_pct, s.cl_pct, s.open_pct, s.fade_pct, s.upper_shadow_pct,
               s.board_pct, s.touched, s.sealed, s.touch_min, s.sealed_min, s.board_runs,
               s.first_seal_time::text as first_seal_time,
               s.peak_time::text as peak_time, s.first5_time::text as first5_time,
               s.peak_min_no, s.speed15_pct, s.above5_min, s.after5_dd_pct,
               s.above_vwap_pct, s.tail_above_vwap, s.vol_above5_share, s.fade_vol_ratio,
               s.gain20_pct, s.is_hi60_break, s.bars_used, s.minute_available,
               array_to_string(s.spike_tags, ',') as tags, s.missing,
               s.calculated_at, f.n1 as next_pct,
               (exp(f.l3) - 1) * 100 as next3_pct, (exp(f.l5) - 1) * 100 as next5_pct,
               (f.n1 is not null) as has_next
          from """ + TABLE + """ s
          left join reference_data.security sec using (security_code)
          left join f on f.security_code = s.security_code and f.trade_date = s.trade_date
         where s.trade_date = %s and s.spec_version = %s""" + cond + """
         order by s.peak_time asc nulls last, s.hi_pct desc nulls last, s.security_code
         limit %s"""
    rows = _rows(sql, [d] + [d, SP.SPEC_VERSION] + params + [lim])
    if rows is None:
        return _fail('异动筛查', TABLE, d)

    for r in rows:
        for k in ('hi_pct', 'cl_pct', 'open_pct', 'fade_pct', 'upper_shadow_pct', 'board_pct',
                  'speed15_pct', 'after5_dd_pct', 'above_vwap_pct', 'tail_above_vwap',
                  'vol_above5_share', 'fade_vol_ratio', 'gain20_pct',
                  'next_pct', 'next3_pct', 'next5_pct'):
            r[k] = _num(r.get(k), 2 if k.endswith('_pct') else 3)
        for k in ('touch_min', 'sealed_min', 'board_runs', 'peak_min_no', 'above5_min', 'bars_used'):
            r[k] = _int(r.get(k))
        r['touched'] = bool(r.get('touched'))
        r['sealed'] = bool(r.get('sealed'))
        r['minute_available'] = bool(r.get('minute_available'))
        r['tags'] = [t for t in str(r.get('tags') or '').split(',') if t]
        r['tag_labels'] = _labels_of(r.get('tags'))

    tot = _one(f"""select count(*)::int n,
                          count(*) filter (where not minute_available)::int no_minute,
                          count(*) filter (where sealed)::int sealed,
                          count(*) filter (where touched and not sealed)::int blown,
                          max(calculated_at) as_of
                     from {TABLE} where trade_date = %s and spec_version = %s""",
               (d, SP.SPEC_VERSION)) or {}
    return {'available': True, 'date': d, 'rows': rows,
            'total': _int(tot.get('n')), 'minute_missing': _int(tot.get('no_minute')),
            'sealed': _int(tot.get('sealed')), 'blown': _int(tot.get('blown')),
            'as_of': tot.get('as_of'), 'rejected_tags': rejected, 'limit': lim}


# 复盘统计的档位（值用「个点」不用百分号：psycopg 会把串里的 % 当占位符吃掉）
_BUCKETS = """
select k grp, count(*)::int n,
       round(avg(n1)::numeric, 3) avg_next,
       round((percentile_cont(0.5) within group (order by n1))::numeric, 3) med_next,
       round(100.0 * count(*) filter (where n1 > 0)
             / nullif(count(*) filter (where n1 is not null), 0), 1) win_next,
       round((percentile_cont(0.1) within group (order by n1))::numeric, 2) p10_next,
       round((percentile_cont(0.9) within group (order by n1))::numeric, 2) p90_next,
       round(((exp(avg(l3)) - 1) * 100)::numeric, 3) avg_next3,
       round(((exp(avg(l5)) - 1) * 100)::numeric, 3) avg_next5,
       round(100.0 * count(*) filter (where l5 > 0)
             / nullif(count(*) filter (where l5 is not null), 0), 1) win_next5,
       round(avg(sealed_min)::numeric, 1) avg_sealed_min
  from e cross join lateral (values
    ('幅度 5~7 个点（未到板）', hi_band = 1),
    ('幅度 7~10 个点（未到板）', hi_band = 2),
    ('幅度 10~15 个点（未到板）', hi_band = 3),
    ('幅度 15~20 个点（未到板）', hi_band = 4),
    ('触及涨停价、收盘没封住', touched and not sealed),
    ('触及涨停价并封住', sealed),
    ('速度 慢拉（15 分钟内不足 4 个点）', speed15_pct is not null and speed15_pct < 4),
    ('速度 快拉（4~7 个点）', speed15_pct >= 4 and speed15_pct < 7),
    ('速度 急拉（7 个点以上）', speed15_pct >= 7),
    ('时刻 高点在 10:00 前', peak_time < '10:00'),
    ('时刻 高点在 10:00~11:30', peak_time >= '10:00' and peak_time <= '11:30'),
    ('时刻 高点在 13:00~14:30', peak_time >= '13:00' and peak_time < '14:30'),
    ('时刻 高点在 14:30 后（尾盘冲）', peak_time >= '14:30'),
    ('承接 峰值后 5 分钟守住（回撤不足 1 个点）', after5_dd_pct > -1),
    ('承接 峰值后回撤 1~3 个点', after5_dd_pct between -3 and -1),
    ('承接 峰值后回撤超 3 个点', after5_dd_pct < -3),
    ('封住 全程没开板（封住段数 = 1）', sealed and board_runs = 1),
    ('封住 开了又回封（段数 >= 2）', sealed and board_runs >= 2),
    ('封住时长 200 分钟以上', sealed_min > 200),
    ('封住时长 121~200 分钟', sealed_min between 121 and 200),
    ('封住时长 31~120 分钟', sealed_min between 31 and 120),
    ('封住时长 1~30 分钟', sealed_min between 1 and 30),
    ('位置 低位（前 20 场累计下跌）', gain20_pct < 0),
    ('位置 横盘（前 20 场涨 0~20 个点）', gain20_pct >= 0 and gain20_pct < 20),
    ('位置 中位（涨 20~50 个点）', gain20_pct >= 20 and gain20_pct < 50),
    ('位置 高位（涨 50 个点以上）', gain20_pct >= 50),
    ('位置 当日盘中创 60 日新高', is_hi60_break),
    ('位置 没创 60 日新高', not is_hi60_break),
    ('回落深度 收盘贴着高点（不足 1 个点）', fade_pct > -1),
    ('回落深度 收盘离高点 1~3 个点', fade_pct between -3 and -1),
    ('回落深度 收盘离高点 3~6 个点', fade_pct between -6 and -3),
    ('回落深度 收盘离高点超 6 个点', fade_pct < -6)
  ) as t(k, hit) where hit and n1 is not null
 group by 1 order by 1
"""


def review(date_from: str = '', date_to: str = '', days: int = 43) -> dict:
    """「最近 N 个月冲高之后走势怎么样」的那张统计表 + 同期基线。

    `days` 是交易日数（默认 43 ≈ 两个月，与用户点名的口径一致）。窗口右端固定取表里最新一场。
    """
    end = _dash(date_to) or _latest(TABLE, spec=SP.SPEC_VERSION)
    if not end:
        return _fail('异动筛查复盘', TABLE, None)
    if date_from:
        start = _dash(date_from)
    else:
        ds = _rows("""select distinct trade_date::text d from market_data.stock_daily
                       where trade_date <= %s order by d desc limit %s""", (end, max(2, int(days))))
        start = ds[-1]['d'] if ds else end

    ev = _FWD + """,
         e as (select s.*, f.n1, f.l3, f.l5,
                      (case when s.hi_pct < 7 then 1 when s.hi_pct < 10 then 2
                            when s.hi_pct < 15 then 3 else 4 end) hi_band
                 from """ + TABLE + """ s
                 left join f on f.security_code = s.security_code and f.trade_date = s.trade_date
                where s.trade_date between %s and %s and s.spec_version = %s),
         base as (""" + _BASE_COLS + """ from f where trade_date between %s and %s and n1 is not null)
    select (select n from base) bn, (select avg_next from base) b_avg, (select med_next from base) b_med,
           (select win_next from base) b_win, (select p10_next from base) b_p10,
           (select p90_next from base) b_p90, (select avg_next5 from base) b_avg5,
           (select win_next5 from base) b_win5,
           (select count(*)::int from e) events,
           (select count(*)::int from e where not minute_available) no_minute,
           (select count(distinct trade_date)::int from e) day_count,
           jsonb_agg(jsonb_build_object('grp', x.grp, 'n', x.n, 'avg_next', x.avg_next,
                                        'med_next', x.med_next, 'win_next', x.win_next,
                                        'p10_next', x.p10_next, 'p90_next', x.p90_next,
                                        'avg_next3', x.avg_next3, 'avg_next5', x.avg_next5,
                                        'win_next5', x.win_next5, 'avg_sealed_min', x.avg_sealed_min)
                     order by x.grp) buckets
      from (""" + _BUCKETS + """) x"""
    r = _one(ev, (start, start, end, SP.SPEC_VERSION, start, end))
    if not r:
        return _fail('异动筛查复盘', TABLE, end)
    raw = r.get('buckets')
    # jsonb 出口有两种形状：psycopg 的 json 解码器直接给 list，`_plain` 那条路给 str。
    # 只认一种就会在其中一种下抛 "the JSON object must be str, bytes..."，整屏 502。
    buckets = json.loads(raw) if isinstance(raw, str) else list(raw or [])
    for b in buckets:
        for k in ('avg_next', 'med_next', 'win_next', 'p10_next', 'p90_next',
                  'avg_next3', 'avg_next5', 'win_next5', 'avg_sealed_min'):
            b[k] = _num(b.get(k), 3)
            if b[k] is None:
                b.pop(k)
    return {'available': True, 'window': {'from': start, 'to': end,
                                          'sessions': _int(r.get('day_count'))},
            'events': _int(r.get('events')), 'minute_missing': _int(r.get('no_minute')),
            'baseline': {'n': _int(r.get('bn')), 'avg_next': _num(r.get('b_avg'), 3),
                         'med_next': _num(r.get('b_med'), 3), 'win_next': _num(r.get('b_win'), 1),
                         'p10_next': _num(r.get('b_p10'), 2), 'p90_next': _num(r.get('b_p90'), 2),
                         'avg_next5': _num(r.get('b_avg5'), 3), 'win_next5': _num(r.get('b_win5'), 1)},
            'buckets': buckets}


def calendar(start: str, end: str) -> list:
    """窗口内每一天有几条异动，以及哪些天**根本没算**（回算没到 ≠ 那天没冲高）。"""
    rows = _rows(f"""select trade_date::text d, count(*)::int n,
                             count(*) filter (where not minute_available)::int no_minute
                        from {TABLE}
                       where trade_date between %s and %s and spec_version = %s
                    group by 1 order by 1 desc""", (start, end, SP.SPEC_VERSION)) or []
    sessions = _rows("""select distinct trade_date::text d from market_data.stock_daily
                         where trade_date between %s and %s order by d desc""",
                     (start, end)) or []
    got = {r['d']: r for r in rows}
    out = []
    for s in sessions:
        r = got.get(s['d'], {})
        out.append({'date': s['d'], 'events': _int(r.get('n')) or 0,
                    'minute_missing': _int(r.get('no_minute')) or 0,
                    'computed': bool(r)})
    return out


def screen(date: str = '', tags: str = '', min_hi: float | None = None, limit: int = 400,
           days: int = 43) -> dict:
    """接口出口：一屏要的全部。不可用时只交 available/reason，不带 rows。"""
    b = board(date, tags=tags, min_hi=min_hi, limit=limit)
    if not b.get('available'):
        return b
    rv = review(date_to=b['date'], days=days)
    cal = {}
    if rv.get('available'):
        cal = calendar(rv['window']['from'], rv['window']['to'])
    caveats = _caveats('coverage') + _cache_caveat(('coverage:intraday_spike',))
    gap = [c['date'] for c in (cal or []) if not c.get('computed')]
    if gap:
        caveats.append({'code': 'spike_not_computed',
                        'text': f'复盘窗口 {rv.get("window", {}).get("from")} ~ '
                                f'{rv.get("window", {}).get("to")} 共 {len(cal)} 场，'
                                f'其中 {len(gap)} 场这个判据还没算（最近缺的是 {gap[0]}）—— '
                                '这些天在各档位里计数为 0 是「还没算」，不是「没有冲高」。'
                                '补算：python -m app.compute.intraday_spike --since <日期> --skip-done'})
    if b.get('rejected_tags'):
        caveats.append({'code': 'spike_tag_rejected',
                        'text': '标签筛选里这些值不是已登记的标签：' + '、'.join(b['rejected_tags'])
                                + ' —— 它们没有参与筛选。可用的是 ' + '、'.join(TAG_KEYS)})
    if _int(b.get('minute_missing')):
        caveats.append({'code': 'spike_no_minute',
                        'text': f"这一场有 {b['minute_missing']} 条异动没有分钟级读数"
                                '（当天 1 分钟线没入库）：速度、高点时刻、峰值后承接、封住时长'
                                '这些列都是空的，母体里「15 分钟内拉升 >=4 个点」那一刀对它们没生效，'
                                '按水平档收进来了。不是「这些票没冲高」'})
    return {**b,
            'caveats': caveats,
            'review': rv if rv.get('available') else {'available': False,
                                                      'reason': rv.get('reason')},
            'calendar': cal,
            'thresholds': {'level_min_pct': SP.LEVEL_MIN_PCT,
                           'speed_min_pct': SP.SPEED_MIN_PCT, 'fast_pct': SP.FAST_PCT,
                           'hold_dd_pct': SP.HOLD_DD_PCT, 'speed_window_min': SP.SPEED_WINDOW,
                           'tail_from': SP.TAIL_FROM.strftime('%H:%M'),
                           'seal_tol_yuan': SP.SEAL_TOL},
            'tag_labels': TAG_LABELS,
            'spec_version': SP.SPEC_VERSION,
            'calibers': {
                'mother': ('母体：日内最高较前收 >= 5 个点，且（15 分钟内拉升 >= 4 个点 或 触及涨停价）。'
                           '用户 2026-10-13 选定的「水平＋速度组合」'),
                'peak_time': ('日内最高出现在几点几分。1 分钟 bar 是右端标注：09:35 那根覆盖 09:30~09:35。'
                              '排序 = 这一列升序（用户点名要的时间顺序）'),
                'sealed': ('封住 = |分钟收盘 - 当日涨停价| <= 0.005 元（A 股最小变动 0.01）。'
                           '封住分钟数就是"保持在那里"的时长；用 0.5% 容差会把接近板误算成封住，不用'),
                'next_pct': ('次日/3 日/5 日由读侧现算：lead(pct_change) 与 exp(sum(ln(1+涨幅/100)))-1 复利合成，'
                             '不拿 close 自己相除（除权日会把跌算成涨），也不用 lag（那是前一天）'),
                'baseline': ('每个统计档都要与同期基线并排读：2026-08-14~10-13 两个月基线是次日 '
                             '−0.06%／上涨 46.0%，而 09-22~10-12 那 9 场是 −0.56%／37.9%'),
                'no_signal': ('实测无区分度的两把尺子（所以不给它们加"信号"徽章）：收盘回落深度、'
                              '全天均价线上方占比'),
            },
            'generated_at': dt.datetime.now().isoformat(timespec='seconds')}
