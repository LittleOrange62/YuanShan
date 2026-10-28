# -*- coding: utf-8 -*-
"""趋势股榜的读侧（接口出口）：只读 `analytics.trend_stock_daily`，**不算判据**。

判据只住 `app/compute/trend_stock.py` 一处；这一层的职责是三件：
1. 把「哪一天」定下来（页面不给日期就用库里这一版算过的最新一场，并**说清是不是退档了**）；
2. 按上市板块分成主板 / 创业板 / 科创板三组，组内按当日涨幅降序（空组也留标题，页面据此写「当日暂无」）；
3. 把每行的**相关板块**补上（所属行业/概念里当日涨幅最高的三个）—— 那是现读 `sector_daily`，
   不是判据的一部分，所以不进表。

三条不许破的读法（写在这里是为了改的人别顺手抹掉）：
  · 界面不许自己按行再算一遍"整组均值"：`summary` 由本层按**同一批过滤条件**算好交出。
  · 缺的读数给 `null` 不给 0，也不许拿邻近交易日顶。
  · 这一屏是客观公开统计，不打分、不预测、不给买卖建议；`caveats` 里那句"当天涨停已被排除"
    必须与板块卡片里那种"可以包含当天涨停"的趋势强势股分开读，两者不是同一份名单。
"""
from __future__ import annotations

from .quant import _dash, _fail, _int, _latest, _num, _one, _reject, _rows

SPEC_VERSION = 'trend-1'          # 与 app/compute/trend_stock.py 同一份；改判据两边一起升
TABLE = 'analytics.trend_stock_daily'
BOARDS = ('主板', '创业板', '科创板')
LOOKBACKS = (5, 10)

CALIBER = (
    '入选四条同时成立：① 所选周期内每一个交易日的收盘价都严格高于「截止这一天的 5 日收盘均线」；'
    '② 当天收盘没有涨停；③ 当日涨幅算得出来；④ 能归进主板／创业板／科创板（北交所不纳入）。'
    '均线是收盘的 5 日简单平均，不复权；「严格高于」不含等于。'
    '10/20 日新高比的是**前 N 根 K 线的最高价**（不含当日），与历史新高、与含当日的自然日新高都不是一个尺子。'
    '放量两口径：当日量>前一交易日量；当日量>前 5 个交易日量的平均（同样不含当日）。'
    '流通市值 = 当日有效流通股本 × 当日收盘，股本按 change_date 分段取。'
)

DEFINITIONS = {
    'board': '上市板块（主板／创业板／科创板）。归属由代码号段判定，北交所整行不进这一屏，不是漏了。',
    'ma5_streak': '截止这一天连续多少个交易日收盘严格站上当日 MA5。掉线那天记 0。'
                  '它与 shape（整段窗口一天都没掉过）不是同一个问题：中间掉一天，streak 会从 1 重新数，shape 直接不成立。',
    'shape': '所选周期内每一天收盘都严格高于当日 MA5 —— 这一列是真入选条件，页面不再单独显示。',
    'nh_10': '当日收盘 > 前 10 根 K 线的最高价（max(high)，不含当日）。',
    'nh_20': '当日收盘 > 前 20 根 K 线的最高价（max(high)，不含当日）。',
    'vol_gt_prev': '当日成交量 > 前一交易日成交量。',
    'vol_gt_vma5': '当日成交量 > 前 5 个交易日成交量的平均（不含当日；含当日会被自己的巨量抬高基准）。',
    'limit_ups_in_window': '所选周期内收盘涨停的天数，就是页面上「近 N 日 X 板」那一枚。一次都没有就不显示。',
    'float_mv_yi': '流通市值（亿）= 当日有效流通股本 × 当日收盘 ÷ 1e8。股本按 change_date 分段取，'
                   '没有分段记录的票退回当前快照折算 —— 那一档是近似。',
    'amount_yi': '当日成交额（亿）。',
    'pct_change': '(当日收盘 − 前一交易日收盘) ÷ 前一交易日收盘，单位 %。',
    'sectors': '该股所属通达信行业与概念里，**当日 880 指数涨幅最高的三个**（名称旁给该指数涨幅）。',
    'bars_used': '截止这一天它在本地日线里一共有几根。不足 9 根时「连续 5 天各自过 MA5」根本判不了，'
                 '这一行按口径不进榜（reject 记 bars_lt_9）。',
    'security_code': '股票代码（带市场后缀）。',
    'name': '股票名称，来自 reference_data.security；字典里没登记中文名的票这里就是代码本身。',
}


def _date_arg(date: str) -> str:
    return (date or '').strip().replace('-', '')


def _sectors_for(codes: list, d: str) -> dict:
    """每只票的相关板块：所属行业/概念里当日涨幅最高的三个。

    时点是**那一天**的成分（`in_date <= 那天 < out_date`），不是今天的成分 ——
    拿今天的名单回算过去的板块涨幅是未来函数。
    """
    if not codes:
        return {}
    rows = _rows("""
        with m as (
            select sm.security_code, si.sector_code, si.name, si.sector_type,
                   sd.pct_change
              from reference_data.sector_member sm
              join reference_data.sector_info si using (sector_code)
              join market_data.sector_daily sd on sd.sector_code = si.sector_code
                                               and sd.trade_date = %s::date
             where sm.security_code = any(%s)
               and si.sector_type in ('行业', '概念')
               and sm.in_date <= %s::date
               and (sm.out_date is null or sm.out_date > %s::date)
               and sd.pct_change is not null),
        r as (select security_code, sector_code, name, sector_type, pct_change,
                     row_number() over (partition by security_code order by pct_change desc) rn
                from m)
        select security_code,
               jsonb_agg(jsonb_build_object('code', sector_code, 'name', name,
                                            'type', sector_type, 'pct', round(pct_change::numeric, 2))
                         order by pct_change desc) sectors
          from r where rn <= 3 group by 1""", (d, codes, d, d)) or []
    return {r['security_code']: (r['sectors'] if isinstance(r['sectors'], list) else []) for r in rows}


def _caveat_list(truncated: bool, short_bars: int, no_mv: int) -> list:
    """caveat 的措辞在这一层一处写好，页面只念不编。每条都带 code，前端按 code 决定摆哪。"""
    out = [
        {'code': 'not_signal',
         'text': '这是客观公开统计：不打分、不预测、不给买卖建议。入选只描述"过去 N 天每天都站上了自己的 5 日线，'
                 '并且今天没封涨停"，不说明接下来会怎样。'},
        {'code': 'vs_sector_card',
         'text': '这一屏与板块卡片里的「趋势强势股」不是同一份名单：卡片固定看 5 天、**可以**包含当天涨停的票，'
                 '本页可选 5 或 10 天、**排除**当天涨停。两份数对不上是口径不同，不是有一边算错。'},
        {'code': 'st_vs_strict',
         'text': '「连续站上天数」（ma5_streak）与"整个窗口一天都没掉过"（入选条件）不是一个问题的答案：'
                 '中间掉一天，前者从 1 重新数，后者整档不成立。'},
    ]
    if short_bars:
        out.append({'code': 'bars_lt_9',
                    'text': f'这一场有 {short_bars} 行往前凑不满 9 根收盘价（多为次新或长期停牌）——'
                            '按口径"连续 5 天各自过 5 日线"根本判不了，这些行一律不进榜，'
                            '界面也不拿手头的几根凑一个偏高的均线。'})
    if no_mv:
        out.append({'code': 'no_mv',
                    'text': f'这一场有 {no_mv} 行没有流通市值（本机对这只票既没有分段股本记录、'
                            '当前快照也是空的）—— 那一格留空，不按 0 排。'})
    if truncated:
        out.append({'code': 'truncated',
                    'text': '榜被上限截断了：下面交出的行数少于这一场实际入选的只数，'
                            '所以组内计数与「整组汇总」只代表已交出的这一段，不代表全场。'})
    return out


def _summary(day: str, sel: str, lu: str) -> dict:
    """整组汇总：在**库里按同一批过滤条件**聚合，不拿被 limit 截断过的那几十行再算一遍。

    页面自己按行算出来的"整组均值"其实是"前 N 行的均值"，那正是"看着像汇总、其实是榜的第一屏"
    那类假数 —— 所以这一段只读 SQL 的聚合结果，并在 `scope` 里把母体只数写出来。
    """
    r = _one(f"""select count(*)::int                                   n,
                       round(avg(pct_change)::numeric, 2)              mean_pct,
                       round(percentile_cont(0.5) within group (order by pct_change)::numeric, 2) med_pct,
                       round(avg(amount_yuan / 1e8)::numeric, 2)       mean_amount_yi,
                       round(avg(float_mv_yuan / 1e8)::numeric, 2)     mean_float_mv_yi,
                       count(*) filter (where nh_10)::int              cnt_nh_10,
                       count(*) filter (where nh_20)::int              cnt_nh_20,
                       count(*) filter (where vol_gt_prev)::int        cnt_vol_gt_prev,
                       count(*) filter (where vol_gt_vma5)::int        cnt_vol_gt_vma5,
                       count(*) filter (where coalesce({lu}, 0) > 0)::int cnt_with_limit_up,
                       count(distinct board)::int                      boards_present
                  from {TABLE}
                 where trade_date = %s and spec_version = %s and {sel}""",
             (day, SPEC_VERSION)) or {}
    n = _int(r.get('n')) or 0
    return {'n': n,
            'scope': f'这一场全部入选的 {n} 只（不是榜上截断后的那一段）' if n else '这一场没有入选的票',
            'mean_pct': _num(r.get('mean_pct'), 2), 'median_pct': _num(r.get('med_pct'), 2),
            'mean_amount_yi': _num(r.get('mean_amount_yi'), 2),
            'mean_float_mv_yi': _num(r.get('mean_float_mv_yi'), 2),
            'cnt_nh_10': _int(r.get('cnt_nh_10')), 'cnt_nh_20': _int(r.get('cnt_nh_20')),
            'cnt_vol_gt_prev': _int(r.get('cnt_vol_gt_prev')), 'cnt_vol_gt_vma5': _int(r.get('cnt_vol_gt_vma5')),
            'cnt_with_limit_up': _int(r.get('cnt_with_limit_up')),
            'boards_present': _int(r.get('boards_present'))}


def screen(date: str = '', lookback: int = 5, limit: int = 300) -> dict:
    """一屏要的全部。不可用时只交 available/reason，**不带 rows 键**。"""
    if lookback not in LOOKBACKS:
        return _reject('lookback', None, f'只支持 {LOOKBACKS} 个交易日，给了 {lookback}')
    d = _date_arg(date)
    latest = _latest(TABLE, SPEC_VERSION)
    if not latest:
        return _fail('趋势股榜', TABLE, None,
                     f'先跑 `python -m app.compute.trend_stock --since …`（加工段步骤名 trendstock）')
    if not d:
        d = latest.replace('-', '')
    day = _dash(d)
    got = _one(f'select count(*)::int n from {TABLE} where trade_date = %s and spec_version = %s',
               (day, SPEC_VERSION))
    if not got or not _int(got.get('n')):
        return _fail('趋势股榜', TABLE, day)

    sel = 'selected5' if lookback == 5 else 'selected10'
    lu = 'limit_ups_5' if lookback == 5 else 'limit_ups_10'
    rows = _rows(f"""
        select t.security_code, coalesce(nullif(s.name, ''), t.security_code) as name,
               t.board, t.close, t.pct_change,
               round(t.amount_yuan / 1e8::numeric, 2)        as amount_yi,
               round(t.float_mv_yuan / 1e8::numeric, 2)      as float_mv_yi,
               t.ma5_streak, t.bars_used, t.nh_10, t.nh_20, t.vol_gt_prev, t.vol_gt_vma5,
               coalesce(t.{lu}, 0)                           as limit_ups_in_window
          from {TABLE} t
          left join reference_data.security s using (security_code)
         where t.trade_date = %s and t.spec_version = %s and t.{sel}
         order by case t.board when '主板' then 0 when '创业板' then 1 when '科创板' then 2 else 3 end,
                  t.pct_change desc nulls last, t.security_code
         limit %s""", (day, SPEC_VERSION, limit)) or []
    for r in rows:
        for k in ('close', 'pct_change', 'amount_yi', 'float_mv_yi'):
            r[k] = _num(r.get(k), 2 if k != 'close' else 3)
        for k in ('nh_10', 'nh_20', 'vol_gt_prev', 'vol_gt_vma5'):
            r[k] = bool(r.get(k)) if r.get(k) is not None else None
        for k in ('ma5_streak', 'bars_used', 'limit_ups_in_window'):
            r[k] = _int(r.get(k))
    sec = _sectors_for([r['security_code'] for r in rows], day)
    for r in rows:
        r['sectors'] = sec.get(r['security_code']) or []

    counts = _one(f"""select count(*)::int universe,
                             count(*) filter (where selected5)::int s5,
                             count(*) filter (where selected10)::int s10,
                             count(*) filter (where reject = 'bars_lt_9')::int short_bars,
                             count(*) filter (where float_mv_yuan is null)::int no_mv,
                             max(calculated_at)::text calc_at
                        from {TABLE} where trade_date = %s and spec_version = %s""",
                  (day, SPEC_VERSION)) or {}
    # 分组只数在**库里**数（榜被上限截断时，页面按行数组出来的组数是"第一屏的组数"，不是全场的）
    grp_rows = _rows(f"""select board, count(*)::int n from {TABLE}
                          where trade_date = %s and spec_version = %s and {sel}
                          group by 1""", (day, SPEC_VERSION)) or []
    groups = {b: 0 for b in BOARDS}
    for g in grp_rows:
        if g.get('board') in groups:
            groups[g['board']] = _int(g.get('n')) or 0
    returned = len(rows)
    total_sel = sum(groups.values())
    truncated = returned < total_sel
    return {
        'available': True,
        'date': day,
        'date_fell_back': bool(date) and _dash(_date_arg(date)) != day,
        'latest_date': _dash(latest),
        'lookback': lookback,
        'spec_version': SPEC_VERSION,
        'min_bars': 9,
        'boards': list(BOARDS),
        'group_counts': groups,
        'group_truncated': truncated,
        'universe_rows': _int(counts.get('universe')),
        'selected_total': total_sel,
        'rows_returned': returned,
        'limit': limit,
        'short_bars_rows': _int(counts.get('short_bars')),
        'no_mv_rows': _int(counts.get('no_mv')),
        'calculated_at': counts.get('calc_at'),
        'caliber': CALIBER,
        'definitions': DEFINITIONS,
        'summary': _summary(day, sel, lu),
        'caveats': _caveat_list(truncated, _int(counts.get('short_bars')) or 0,
                               _int(counts.get('no_mv')) or 0),
        'rows': rows,
    }
