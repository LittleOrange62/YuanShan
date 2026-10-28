# -*- coding: utf-8 -*-
"""名次稳步前进榜 · 后续表现（用户 2026-10-20 凌晨点名）：「昨日前 80 名的进入股价表现
（把各种名次都显示出来，包括最近倍量名次，突破信号等等）」。

这块答的问题：上一场那张「名次稳步前进榜」交出的前 N 名，**从那天之后股价到底走没走出来**，
外加它们母体场次当天各路判据各自的名次/在榜情况。三条已经与用户确认过的口径：

1. **母体** ＝ 上一场（昨日）那张稳步前进榜交出的前 N 名，与它同一档 `metric`（xy / oper 可切）。
   **不是今日那张榜，不是各榜并集**。母体直接调 `quant.rank_momentum(metric, limit, date)` 拿 ——
   判据（窗口 STEADY_SESSIONS、三条门槛、名次读 `analytics.score.rank_in_market`）只住那一处，
   这里一个字都不抄（本文件没有第二份名次 SQL）。
2. **表现窗口** ＝ 「今日涨跌 + 近 5 日涨跌」两栏，不逐日全铺：这张榜每天换人，逐日全铺会出现几百列
   （逐日那一族屏已有先例 `quant.energy_track`，形状不同、问题不同，不合并）。
   ⚠️ 2026-10-27 用户点名改的两处措辞与一列换尺：
     · 母体默认是**前一场**那张榜 ⇒ 母体之后的第一场行情**就是今天**，所以这一列在屏上叫
       「今日涨跌」，不叫「次日涨跌」（旧名让人以为要等到明天才有数）。
       键名仍是 `chg_next_day`／`next_day`（＝基准后第一场）—— 只有把基准日指到更早那天时
       它才不是今天，那一格靠「期间场数」那一列自证隔了几场。
     · 「至今累计」撤下：母体是前一场时它与「今日涨跌」是同一个数，摆两列是假的两把尺。
       换成**近 5 日涨跌**（读至日那一场收盘 ÷ 往前第 5 场那一场收盘 − 1，跨 5 个市场场次、含今天），
       与首板分析屏的「近 5 场」同一个出口、同一把尺（`day_returns.returns_by_code(span=5)`）。
       **它不以母体日为起点** ⇒ 两列不许相减。
   涨跌幅的尺子只住 `duanxian/day_returns.py`（后复权 `close × adjustment_factor`，按市场场次找上一场，
   停牌不许桥接），这里不自己写 SQL 算涨幅。
3. **基准日默认 ＝ 库里最新有名次场次的前一场**（不是最新一场）——母体那天之后必须至少有一场行情，
   才存在「今日涨跌」这个读数；这件事随返回体 caveats 原样说清，不让人在屏上猜。

「各种名次」的读数源（2026-10-20 用 `information_schema.columns` 逐列实查过列名）：

| 列 | 出处 | 那天的分母 |
|---|---|---|
| `oper_rank` 综合分全场名次 | `analytics.score.rank_in_market`（capability_key='oper_score'） | 当日有名次的只数 |
| `quadrant`/`quadrant_label` 四维象限 | `analytics.score.quadrant`（母体行已带，不重查判据） | 同上 |
| `dbl_rank`/`dbl_energy` 倍量加权分全场名次 | `analytics.volume_double` 同场同批（energy_fp），表达式 `quant._energy_rank_expr` | 当日该批有分的只数 |
| `breakout_type` 突破信号 | `analytics.volume_breakout_signal`（spec 引 `app/compute/breakout_signal.SPEC_VERSION`） | 当日该版本行数 |
| `ambush_rank_in_board` 埋伏**榜内**名次 | `analytics.ambush_setup`（这张表只落命中的票 ⇒ **榜内名次，不是全场名次**，与上面两把尺不许混在一个列名里卖） | 当日命中埋伏形态的只数 |
| `abnormal_hit` 异动筛查母体 | `analytics.abnormal_move_daily`（universe_by 中文只引 `abnormal_view.UNIVERSE_LABEL`） | 当日提醒母体行数 |
| `trend_selected5/10` 趋势股入选 | `analytics.trend_stock_daily` 的两个布尔列 | 当日行数 |

诚实性三条（照 `energy_track` 那一族的写法）：**哪一族的表那天没行，该族整列交 null（界面 —），
并点名是「那天这一族没跑/没数」还是「这些票都不在榜」** —— 0 与「否」都是实质读数，不许拿它们顶「没读到」；
缺任何一边的涨跌幅一律 null 并带原因码（`perf_missing_reason`）；名次规则文案只引
`duanxian/ranking.py` 的 `RULE_NOTE`/`RULE_NOTE_SHORT`（随 `rank.rule` 交出，本文件不另写一句）。
"""
from __future__ import annotations

from . import day_returns
from . import quant as Q
from . import ranking as RANKING

#: 分页每页行数（默认值；界面上的档按钮读它，不各写一份）。
PAGE_SIZE = 25

#: 近五日那一列的窗口（**市场场次**，不含基期那一根共 5 场）：与首板分析屏的「近 5 场」
#: 同一个出口 `day_returns.returns_by_code(span=5)`，一把尺只量一次。
#: 注意 `day_returns` 的规矩：span=5 取的是「目标日 + 往前 5 场」＝窗口最多 6 根日线，
#: 基期是往前第 5 场那一日 ⇒ `bars_5d` 满档是 6，不是 5。
PERF_SPAN = 5

#: `perf_missing_reason` 的原因码文案。只住这一处，行上随 `perf_missing_note` 交出，界面不自己拼。
_PERF_NOTES = {
    'no_next_session': '母体场次之后库里还没有下一场行情 —— 今日涨跌幅算不出来，这一格是 —，'
                       '不是 0%（0% 是一分钱没动）。近 5 日那一列不依赖母体日（它看读至日往回 5 场），'
                       '所以它可能有数而这一格没有，两列各读各的。',
    'no_price_after': '从母体后第一场到读至日，这只票在日线里一行都没有（长期停牌或未上市）—— 这一格是 —，不是 0%。',
    'no_row_on_next_day': '母体后第一场它没有行（那天停牌）—— 不许拿后面某一场冒充「今日」，'
                          '所以这一格是 —；近 5 日按它自己有的那些行算，两边各读各的。',
    'missing_base': '基期取不到：母体场次那一场它在日线里没有行，后复权价没有参照点 —— '
                    '这一格是 —（`day_returns` 的规矩：停牌不许桥接，桥了就是把跨场的波动说成单场）。',
    'perf5_unread': '近 5 日这一发查询没读回来（库不通或查询失败）—— 整列交 —，'
                    '这是「没读到」，不是「这些票五日内都没动」。',
    'no_bars_in_window': f'读至日往前 {PERF_SPAN + 1} 场里这只票一根日线都没有（新上市或整段停牌）—— '
                         f'窗口凑不齐就没有「近 {PERF_SPAN} 日」，这一格是 —，不拿更长的窗口凑数。',
    'missing_base5': f'近 {PERF_SPAN} 日的基期（读至日往前第 {PERF_SPAN} 场那天的收盘）没有它的行 —— 宁可交 —，'
                     f'也不拿更早或更晚那天冒充基期（那会把跨场波动算进 {PERF_SPAN} 日里）。',
    'no_row_on_to_date': f'读至日（{PERF_SPAN} 日那一段的最后一天）它没有行（今天停牌）—— 终点都没有，'
                         '累计涨跌幅就没有落点，这一格是 —；不拿它前面那场冒充读至日（那是「近 4 日」不是「近 5 日」）。',
}

#: 倍量名次列没数时的原因码（与 `dbl_rank_missing_reason` 一一对应）。
_DBL_NOTES = {
    'unread': '倍量这一发查询没读回来（库不通或查询失败），不是这些票没有倍量分。',
    'no_batch': '这一场的倍量行与当前生效档表指纹对不上（多半是改了档表还没重算完）—— '
                '整列交 — 是「这批数还没按新尺算」，不是「这些票都没有倍量分」。',
    'no_row': '倍量表里这一天没有这只票的行（窗口不足或那天没进统计）—— — 不是 0 分。',
    'no_energy': '有行但当日加权分为空（缺 K 线）—— 没有分就没有名次，这一格是 —，不是「最后一名」。',
}


def _day_counts(d: str, fp: str, bs_spec: str, amb_spec: str, abn_spec: str,
                trn_spec: str) -> dict | None:
    """各路名次在**母体场次那一场**实有多少行（一次读齐，界面与 caveat 都念这份现数）。

    0 行与读不到是两句话：读不到返回 None（调用方按「没读到」处理，不拿 0 顶）。
    """
    return Q._one("""select
        (select count(*)::int from analytics.volume_double
          where trade_date = %s and energy_fp = %s) dbl_rows,
        (select count(*)::int from analytics.volume_double
          where trade_date = %s and energy_fp = %s and dbl_energy is not null) dbl_scored,
        (select count(*)::int from analytics.score
          where trade_date = %s and capability_key = 'oper_score'
            and rank_in_market is not null) oper_scored,
        (select count(*)::int from analytics.volume_breakout_signal
          where trade_date = %s and spec_version = %s) breakout_rows,
        (select count(*)::int from analytics.ambush_setup
          where trade_date = %s and spec_version = %s) ambush_rows,
        (select count(*)::int from analytics.abnormal_move_daily
          where trade_date = %s and spec_version = %s) abnormal_rows,
        (select count(*)::int from analytics.trend_stock_daily
          where trade_date = %s and spec_version = %s) trend_rows""",
                  (d, fp, d, fp, d, d, bs_spec, d, amb_spec, d, abn_spec, d, trn_spec))


def board(metric: str = 'xy', date: str = '', limit: int = 80, refresh: bool = False) -> dict:
    """上一场稳步前进榜前 N 名的「今日 + 近 5 日」股价表现与各路名次。

    `metric` 只有 xy / oper 两档（与母体榜同一档、界面上就是那两个按钮）；坏值由母体
    `rank_momentum` 的 `_reject` 原样拒掉并透传 —— 那一句「只收 xy、oper」不许有第二份抄本，
    也不许被 `_fail` 的「这一场没有数据」冒充。
    """
    # 判据版本与中文标签一律引加工侧/读侧那一份常量，这里不抄第二份。
    from app.compute import abnormal_move as abn
    from app.compute import ambush_setup as amb
    from app.compute import breakout_signal as bs
    from app.compute import trend_stock as ts
    from . import abnormal_view as abnv

    if refresh:
        Q.clear_agg_cache('rank_momentum_')

    # ---- 1. 基准日（母体场次）：默认＝库里最新有名次场次的前一场 ----
    d = Q._dash(date)
    if not d:
        r = Q._one("""select min(trade_date)::text d from (
                       select distinct trade_date from analytics.score
                        where capability_key = 'oper_score' and rank_in_market is not null
                        order by trade_date desc limit 2) x""")
        if r is None:
            return Q._fail('名次稳步前进榜后续表现', 'analytics.score', None,
                           '基准日这一发没读回来（库不通或查询失败），不是库里没有场次')
        d = (r or {}).get('d') or ''
    if not d:
        return Q._fail('名次稳步前进榜后续表现', 'analytics.score', None)

    # ---- 2. 母体：直接调 rank_momentum，判据不许有第二份抄本 ----
    parent = Q.rank_momentum(metric=metric, limit=limit, date=d, refresh=refresh)
    if not parent.get('available'):
        # 母体被拒（坏 metric）或被判定这场没数，都先把那句原话带出去，
        # 不在这边另造句 —— 「参数不对」与「库里没这场」是两句话。
        return {'available': False, 'date': d, 'parent_date': d, 'metric': metric,
                'reason': '母体「名次稳步前进榜」先要能交出来：' + str(parent.get('reason') or '')}
    m = parent.get('metric')
    pd = (parent.get('window') or {}).get('last_day') or d   # 母体榜真正的末日（=基准日）
    prows = parent.get('rows') or []
    codes = [str(r.get('security_code')) for r in prows if r.get('security_code')]

    # ---- 3. 表现窗口（市场场次口径；尺子只住 day_returns） ----
    nxt = Q._one("select min(trade_date)::text d from market_data.stock_daily where trade_date > %s", (pd,))
    if nxt is None:
        return Q._fail('名次稳步前进榜后续表现', 'market_data.stock_daily', pd,
                       '基准后第一场（就是屏上那一列「今日涨跌」）这一发查询没读回来（库不通或查询失败）')
    next_day = nxt.get('d')
    hi = Q._one("select max(trade_date)::text d from market_data.stock_daily")
    to_date = (hi or {}).get('d')
    wsess = Q._one("""select count(*)::int n from (
                       select distinct trade_date from market_data.stock_daily
                        where trade_date > %s and trade_date <= %s) x""", (pd, to_date or pd))
    sessions_after = Q._int(wsess and wsess.get('n')) if wsess is not None else None

    # ---- 4. 各路名次当日行数（0 与没读到的分界全靠这份现数） ----
    fp = Q.venc.fingerprint()
    counts = _day_counts(pd, fp, bs.SPEC_VERSION, amb.SPEC_VERSION, abn.SPEC_VERSION, ts.SPEC_VERSION)
    if counts is None:
        return Q._fail('名次稳步前进榜后续表现', 'analytics.volume_double', pd,
                       '各路名次当日行数这一发没读回来（库不通或查询失败），不是那些表那天没数')

    dbl_map: dict = {}
    brk_map: dict = {}
    amb_map: dict = {}
    abn_map: dict = {}
    trn_map: dict = {}
    scr_map: dict = {}
    unread: list[str] = []
    if codes:
        # 倍量：**当日全场**名次（相关子查询数「同场同批分更高的有几只」，与倍量榜同一个表达式；
        # 不用 rank() over —— 那是 where 之后算的，加了票filter就变成「这几只里的第几名」）。
        dbl_rows = Q._rows(f"""select v.security_code as code, v.dbl_energy,
                                      case when v.dbl_energy is null then null else
                                           {Q._energy_rank_expr('v')} end as dbl_rank
                                 from analytics.volume_double v
                                where v.trade_date = %s and v.energy_fp = %s
                                  and v.security_code = any(%s::text[])""", (pd, fp, codes))
        if dbl_rows is None:
            unread.append('dbl')
        else:
            dbl_map = {str(r.get('code')): r for r in dbl_rows}
        # 综合分全场名次（母体行只带象限与分值，名次这里按同一天补读）
        scr_rows = Q._rows("""select security_code as code, rank_in_market
                                from analytics.score
                               where trade_date = %s and capability_key = 'oper_score'
                                 and security_code = any(%s::text[])""", (pd, codes))
        if scr_rows is None:
            unread.append('oper')
        else:
            scr_map = {str(r.get('code')): r for r in scr_rows}
        brk_rows = Q._rows("""select security_code as code, signal_type
                                from analytics.volume_breakout_signal
                               where trade_date = %s and spec_version = %s
                                 and security_code = any(%s::text[])""", (pd, bs.SPEC_VERSION, codes))
        if brk_rows is None:
            unread.append('breakout')
        else:
            brk_map = {str(r.get('code')): r for r in brk_rows}
        # 埋伏：**榜内**名次（这张表只落命中的票）；名次走公用组件 `rank_sql`，
        # 窗口前不筛代码（先算整日榜内名次、再取这批票）——先筛就变成「这几只里的第几名」。
        amb_rows = Q._rows(f"""select r.code, r.ambush_types, r.ambush_score, r.rank_in_board
                                  from (select security_code as code, ambush_types, ambush_score,
                                               case when ambush_score is null then null else
                                                    {RANKING.rank_sql('ambush_score desc nulls last')}
                                               end as rank_in_board
                                          from analytics.ambush_setup
                                         where trade_date = %s and spec_version = %s) r
                                 where r.code = any(%s::text[])""", (pd, amb.SPEC_VERSION, codes))
        if amb_rows is None:
            unread.append('ambush')
        else:
            amb_map = {str(r.get('code')): r for r in amb_rows}
        abn_rows = Q._rows("""select security_code as code, universe_by
                                from analytics.abnormal_move_daily
                               where trade_date = %s and spec_version = %s
                                 and security_code = any(%s::text[])""", (pd, abn.SPEC_VERSION, codes))
        if abn_rows is None:
            unread.append('abnormal')
        else:
            abn_map = {str(r.get('code')): r for r in abn_rows}
        trn_rows = Q._rows("""select security_code as code, selected5, selected10
                                from analytics.trend_stock_daily
                               where trade_date = %s and spec_version = %s
                                 and security_code = any(%s::text[])""", (pd, ts.SPEC_VERSION, codes))
        if trn_rows is None:
            unread.append('trend')
        else:
            trn_map = {str(r.get('code')): r for r in trn_rows}

    # ---- 5. 今日（母体后第一场）+ 近 5 场滚动累计（两条查询，尺子都在 day_returns 那一处） ----
    ser: dict = {}
    if codes and next_day and to_date:
        ser = day_returns.series_by_code({c: str(next_day) for c in codes}, str(to_date)) or {}
    # 近 5 场以**读至日**为终点往回数，与母体日无关 ⇒ 母体之后一场都没有时它照样算得出来。
    perf5: dict = {}
    if codes and to_date:
        perf5 = day_returns.returns_by_code(codes, str(to_date), span=PERF_SPAN) or {}

    # ---- 6. 逐行拼装（母体读数 → 表现 → 各路名次；缺的一律 None + 原因码） ----
    rows: list[dict] = []
    for r in prows:
        c = str(r.get('security_code') or '')
        s = ser.get(c) or {}
        days = s.get('days') or []
        chg_next = None
        bars_after = None
        perf_reason = None
        if not next_day or not to_date:
            perf_reason = 'no_next_session'
        elif not days:
            perf_reason = 'no_price_after'
        else:
            bars_after = len(days)
            first = days[0]
            if str(Q.jsonable(first.get('trade_date')) or '') == str(next_day):
                chg_next = Q._num(first.get('pct'))
            if chg_next is None:
                perf_reason = 'missing_base' if s.get('missing_base') else 'no_row_on_next_day'

        # 近 5 日：单独一个原因码（它缺而今日不缺是常态 —— 窗口起点比母体日早得多）
        p5 = perf5.get(c) or {}
        chg_5d = Q._num(p5.get('chg_span'))
        bars_5d = Q._int(p5.get('bars'))
        base5_day = Q.jsonable(p5.get('base_day'))
        perf5_reason = None
        if not perf5:
            perf5_reason = 'perf5_unread'
        elif chg_5d is None:
            # 三种「没数」是三句话：窗口里一根都没有 / 基期那一场没行 / 读至日那天没行。
            # 桩里给 bars=0 但有 base_day 那种组合在 SQL 层出不来，所以这里不认它。
            perf5_reason = ('no_bars_in_window' if not p5
                            else 'missing_base5' if p5.get('base_day') is None
                            else 'no_row_on_to_date')

        dr = dbl_map.get(c)
        if 'dbl' in unread:
            dbl_rank, dbl_energy, dbl_reason = None, None, 'unread'
        elif not Q._int(counts.get('dbl_scored')):
            dbl_rank, dbl_energy, dbl_reason = None, None, 'no_batch'
        elif dr is None:
            dbl_rank, dbl_energy, dbl_reason = None, None, 'no_row'
        elif dr.get('dbl_energy') is None:
            dbl_rank, dbl_energy, dbl_reason = None, None, 'no_energy'
        else:
            dbl_rank = Q._int(dr.get('dbl_rank'))
            dbl_energy = Q._num(dr.get('dbl_energy'), 4)
            dbl_reason = None

        ar = amb_map.get(c)
        types = list(ar.get('ambush_types') or []) if ar else []
        ub = (abn_map.get(c) or {}).get('universe_by') if c in abn_map else None
        abnv_ok = 'abnormal' not in unread and Q._int(counts.get('abnormal_rows'))
        tr = trn_map.get(c)
        trend_ok = 'trend' not in unread and Q._int(counts.get('trend_rows'))
        br = brk_map.get(c)
        brk_ok = 'breakout' not in unread and Q._int(counts.get('breakout_rows'))
        stype = (br or {}).get('signal_type') if brk_ok else None

        rows.append({
            'code': c,
            'name': r.get('name'),
            'board': r.get('board'),
            'board_rank': r.get('board_rank'),
            'first_series': r.get('first_series'),
            'latest_series': r.get('latest_series'),
            'gain_abs': r.get('gain_abs'),
            'gain_frac': r.get('gain_frac'),
            'oper_value': r.get('oper_value'),
            'quadrant': r.get('quadrant'),
            'quadrant_label': r.get('quadrant_label'),
            'matched_total': r.get('matched_total'),
            'next_day': Q.jsonable(next_day),
            'chg_next_day': chg_next,
            'chg_5d': chg_5d,
            'bars_5d': bars_5d,
            'base_5d_day': base5_day,
            'perf5_missing_reason': perf5_reason,
            'perf5_missing_note': _PERF_NOTES.get(perf5_reason or ''),
            'bars_after': bars_after,
            'perf_missing_reason': perf_reason,
            'perf_missing_note': _PERF_NOTES.get(perf_reason or ''),
            'dbl_energy': dbl_energy,
            'dbl_rank': dbl_rank,
            'dbl_missing_reason': dbl_reason,
            'dbl_missing_note': _DBL_NOTES.get(dbl_reason or ''),
            'oper_rank': Q._int((scr_map.get(c) or {}).get('rank_in_market')) if 'oper' not in unread else None,
            'breakout_type': stype,
            'breakout_type_label': bs.TYPE_LABELS.get(stype, stype) if stype else None,
            'ambush_rank_in_board': Q._int((ar or {}).get('rank_in_board')) if ar else None,
            'ambush_type_labels': '、'.join(amb.TYPE_LABELS.get(t, t) for t in types) if types else None,
            'ambush_score': Q._num((ar or {}).get('ambush_score'), 3) if ar else None,
            'abnormal_hit': (c in abn_map) if abnv_ok else None,
            'abnormal_universe_by_label': '、'.join(
                abnv.UNIVERSE_LABEL.get(t.strip(), t.strip())
                for t in str(ub or '').split(',') if t.strip()) if (ub and abnv_ok) else None,
            'trend_selected5': bool(tr.get('selected5')) if (trend_ok and tr) else None,
            'trend_selected10': bool(tr.get('selected10')) if (trend_ok and tr) else None,
        })

    # ---- 7. 返回体 ----
    matched = Q._int(parent.get('total_matched'))
    lim = Q._int(parent.get('limit'))
    caveats: list[dict] = []
    caveats.append({'code': 'follow_scope', 'text': (
        f'母体＝上一场（{pd}）那张「名次稳步前进榜」（{parent.get("series_label")}档）交出的前 '
        f'{lim} 名（与它同一档 metric 可切）—— 不是今天那张榜，也不是各榜并集。'
        f'母体那天之后必须至少有一场行情，'
        f'「今日涨跌」那一列才算得出来；目前读到 {to_date}（库里最新一场行情），'
        f'母体之后共 {sessions_after if sessions_after is not None else "—"} 场。'
        f'「近 {PERF_SPAN} 日涨跌」看的是读至日往回 {PERF_SPAN} 个市场场次，与母体日无关。')})
    caveats.append({'code': 'dash_not_zero', 'text': (
        '— 与 0 是两种读数（全站一条规矩）：缺任何一边（那天停牌、当日没行、没有基期、'
        '那一族榜那天没跑）一律交 null，界面念 —；0、false 与「否」都是实质读数（平盘、'
        '没进这一族榜、没被选中）。哪一族那天整场没数，下面会点名，不拿「否」充数。')})
    if next_day is None:
        caveats.append({'code': 'no_next_session', 'text': _PERF_NOTES['no_next_session'] +
                        f'（基准日 {pd} 就是行情表里最新的一场；等下一场收盘链跑完这一栏才有数。）'})
    if not perf5 and codes:
        caveats.append({'code': 'perf5_unread', 'hard': True, 'text': (
            _PERF_NOTES['perf5_unread'] + f'（读至日 {to_date}，窗口 {PERF_SPAN} 场。）')})
    if sessions_after == 1:
        caveats.append({'code': 'today_is_last_day_of_window', 'text': (
            f'目前母体之后只有 {next_day} 这一场行情 ⇒ 「今日涨跌」正好是「近 {PERF_SPAN} 日」窗口里的'
            f'最后一根。两列不是同一把尺（一个只看这一场、一个是 {PERF_SPAN} 场滚动的累计，'
            '且都是后复权复利），不许把两列相减去推「前面几天涨了多少」。')})
    # 母体自己的声明原样带过来（截断/空榜/陈旧名次/口径断档），一句都不重写。
    keep = {'board_truncated', 'empty_board', 'stale_ranks', 'caliber_break', 'y_axis_levels'}
    for cv in parent.get('caveats') or []:
        if isinstance(cv, dict) and cv.get('code') in keep and cv.get('code') not in {x.get('code') for x in caveats}:
            caveats.append(dict(cv))
    fam_empty = [('dbl', 'breakout_batch', '倍量加权分（当前档表批次）'),
                 ('breakout', 'breakout_session_empty', '突破信号'),
                 ('ambush', 'ambush_session_empty', '埋伏票'),
                 ('abnormal', 'abnormal_session_empty', '异动筛查母体'),
                 ('trend', 'trend_session_empty', '趋势股')]
    key_map = {'dbl': 'dbl_rows', 'breakout': 'breakout_rows', 'ambush': 'ambush_rows',
               'abnormal': 'abnormal_rows', 'trend': 'trend_rows'}
    for fam, code, label in fam_empty:
        if fam in unread:
            caveats.append({'code': f'{fam}_unread', 'text': (
                f'{label}在 {pd} 这一场的查询没读回来（那条取数失败了）—— 相关列全部交 —，'
                '这是「没读到」，不是「这些票都不在榜」。')})
            continue
        n = Q._int(counts.get(key_map[fam]))
        if fam == 'dbl' and not Q._int(counts.get('dbl_scored')):
            caveats.append({'code': 'dbl_batch_empty', 'text': _DBL_NOTES['no_batch'] +
                            f'（生效指纹 {fp}，{pd} 这场带这一批指纹的倍量行为 {n}。）'})
        elif fam != 'dbl' and n == 0:
            caveats.append({'code': code, 'text': (
                f'{pd} 这一场「{label}」的表没有行（那天这一族没跑或还没回填）—— 相关列全部交 —，'
                '这不等于「这些票都不在榜」，也不等于「0 只」。')})
    if pd != d:
        caveats.append({'code': 'parent_shifted', 'text': (
            f'你给的基准日 {d} 不是有名次的场次，母体榜实际取到的是 {pd} 那场（往前数最近一场）。')})
    caveats.extend(Q._caveats('coverage'))
    caveats.extend(Q._cache_caveat(('coverage:score', f'rank_momentum_stats:{pd}:{m}')))

    definitions = {
        'code': '代码（母体行的 security_code，这一屏改叫 code 与本族其它榜单行一致）',
        'name': '名称（读 reference_data.security；字典里没登记中文名时这一格就是代码本身）',
        'board': '板块归属（主板 / 创业板 / 科创板 / 北交所，与母体榜同一列）',
        'board_rank': f'母体那场榜里它排第几行（{pd} 那场、{parent.get("series_label")}档、'
                      f'共 {matched} 只符合判据、接口交出前 {lim} 只）—— 这一列不是全场名次',
        'first_series': f'母体窗口第一场（{(parent.get("window") or {}).get("first_day")}）的{parent.get("series_label")}',
        'latest_series': f'母体场次（{pd}）的{parent.get("series_label")}；名次数字变小＝往前走（1 = 全场第一）',
        'gain_abs': 'first_series − latest_series（名次前进的绝对步数）',
        'gain_frac': '净前进幅度 %（母体榜的排序列；口径与上面那张榜完全同一份，不重算）',
        'oper_value': f'母体场次（{pd}）的综合分原值 —— 名次是相对量，这一列才是绝对量',
        'quadrant': f'母体场次当日的四维象限档（与「选股象限」那一屏同一列，不是新判据）',
        'quadrant_label': '当日象限的中文名（读 config.setting 的 quadrant_labels）',
        'matched_total': f'母体判据当日符合的总只数（榜被 limit 截断时它大于行数；每行同值）',
        'next_day': f'母体场次之后的第一场行情（按市场场次，不是日历日；母体默认是前一场 ⇒ 它就是今天，'
                    f'只有把基准日指到更早那天时它才不是今天）',
        'chg_next_day': '今日涨跌幅 %（母体之后那第一场行情的涨跌幅；母体默认是前一场 ⇒ 那一场就是今天，'
                        '键名仍是 `chg_next_day`（＝基准后第一场），把基准日指到更早那天时它就不是今天，'
                        '那一格由「期间场数」`bars_after` 自证隔了几场。'
                        '尺子：后复权 close × adjustment_factor，只住 day_returns；'
                        '母体场次没有行就交 null —— 停牌不桥接，不许拿更早那场的收盘当基期）',
        'chg_5d': f'近 {PERF_SPAN} 日涨跌幅 %（读至日（{to_date}）那一场收盘 ÷ 往前第 {PERF_SPAN} 场那一场收盘 − 1，'
                  f'含今天在内跨 {PERF_SPAN} 个市场场次；与首板分析屏的「近 {PERF_SPAN} 场」同一个出口同一把尺）。'
                  f'⚠️ 它的起点不是母体日，与「今日涨跌」量的是两段不同的区间 ⇒ 这两列不许相减',
        'bars_5d': f'近 {PERF_SPAN} 日那一段的窗口里这只票真有几根日线（窗口含基期那一场，满档是 '
                   f'{PERF_SPAN + 1} 根；新股与停牌会不足 —— 只有 4 根时，「近 {PERF_SPAN} 日」要读成'
                   f'「这 4 根里的累计」，不是「{PERF_SPAN + 1} 场里累计」）；空着是没算出来，不是 0 根',
        'base_5d_day': f'近 {PERF_SPAN} 日那一段的基期（读至日往前第 {PERF_SPAN} 场那一场）；'
                       '它那天停牌时这一格是 —，涨跌幅那一格也跟着是 —（不冒充基期）',
        'perf5_missing_reason': f'近 {PERF_SPAN} 日那一列读不到的原因码：perf5_unread / no_bars_in_window / '
                                f'missing_base5 / no_row_on_to_date；空＝算出来了（与今日那一列各自独立 —— '
                                '它缺而今日有数是常态）',
        'perf5_missing_note': '上面那个原因码的完整那句话（文案只住 duanxian/momentum_follow.py 一处）',
        'bars_after': f'母体之后到读至日（{to_date}）之间这只票真有行情的场数；空着是没算出来，不是 0 场',
        'perf_missing_reason': '今日涨跌幅读不到的原因码：no_next_session / no_price_after / '
                              'no_row_on_next_day / missing_base；空＝算出来了',
        'perf_missing_note': '上面那个原因码的完整那句话（文案只住 duanxian/momentum_follow.py 一处）',
        'dbl_energy': f'母体场次当日的倍量加权分（analytics.volume_double.dbl_energy，'
                      f'当前生效档表指纹批次）；单位是「分」不是次数',
        'dbl_rank': '倍量加权分的当日全场名次（同分同名、下一名顺延，与倍量榜同一个表达式；'
                    '没有分就没有名次 —— 这一格是 —，不是「最后一名」）',
        'dbl_missing_reason': '倍量名次没数时的原因码：unread / no_batch / no_row / no_energy；空＝有数',
        'dbl_missing_note': '上面那个原因码的完整那句话',
        'oper_rank': f'母体场次（{pd}）综合分（oper_score）的当日全场名次 —— 与 analytics.score.'
                     'rank_in_market 同一个数，不是这一屏新排的',
        'breakout_type': f'突破信号类型原码（first_break / surge_after_shrink / converging；'
                         f'判据与 spec 版本只住 app/compute/breakout_signal.py）；空＝那天它不在信号榜上'
                         '（若那天整族没行，看 breakout_session_empty 声明）',
        'breakout_type_label': '突破信号类型的中文名（读加工侧 TYPE_LABELS，页面不自己翻译）',
        'ambush_rank_in_board': f'埋伏榜榜内名次（analytics.ambush_setup 只落当日命中形态的票，'
                                f'那天共 {Q._int(counts.get("ambush_rows"))} 只）—— 这一列不是全场名次，'
                                '与上面两把尺不许混读；空＝那天它不在埋伏榜上',
        'ambush_type_labels': '它命中的埋伏形态中文名（读加工侧 TYPE_LABELS，顿号分隔）',
        'ambush_score': '埋伏合成分原值（0~100，只为榜内排序、不是涨跌概率；口径读埋伏票那一屏）',
        'abnormal_hit': '是否在当日异动筛查母体里（analytics.abnormal_move_daily 那张提醒榜；'
                        'false＝那天有榜而它没进，是实质读数；null＝那天没跑或没读到，界面 —）',
        'abnormal_universe_by_label': '它进异动母体的是哪一条（中文只读 abnormal_view.UNIVERSE_LABEL 那一份）',
        'trend_selected5': '趋势股连续 5 场入选（analytics.trend_stock_daily.selected5；判据读趋势股那一屏）',
        'trend_selected10': '趋势股连续 10 场入选（同上 selected10）',
    }

    return {
        'available': True,
        'date': Q.jsonable(to_date),
        'parent_date': pd,
        'default_parent_rule': 'date 不给时取「库里最新有名次场次的前一场」—— 母体那天之后要至少'
                               '有一场行情才算得出「今日涨跌」（那一场就是今天）；'
                               '这条随 caveats 上屏，不让人在页面上猜',
        'metric': m,
        'metric_label': parent.get('metric_label'),
        'series_label': parent.get('series_label'),
        'rule': parent.get('rule'),
        'window': {'sessions': (parent.get('window') or {}).get('sessions'),
                   'steps': (parent.get('window') or {}).get('steps'),
                   'first_day': (parent.get('window') or {}).get('first_day'),
                   'last_day': pd, 'next_day': Q.jsonable(next_day),
                   'to_date': Q.jsonable(to_date), 'sessions_after': sessions_after},
        'limit': lim,
        'page_size': PAGE_SIZE,
        'total_matched': matched,
        'thresholds': parent.get('thresholds'),
        'fingerprint': fp,
        'rank': {'rule': RANKING.RULE_NOTE, 'rule_short': RANKING.RULE_NOTE_SHORT,
                 'universe': {'scored': Q._int(counts.get('oper_scored')),
                              'parent_matched': matched, 'board_rows': len(rows),
                              'dbl_scored': Q._int(counts.get('dbl_scored')),
                              'breakout_rows': Q._int(counts.get('breakout_rows')),
                              'ambush_rows': Q._int(counts.get('ambush_rows')),
                              'abnormal_rows': Q._int(counts.get('abnormal_rows')),
                              'trend_rows': Q._int(counts.get('trend_rows')),
                              'note': '这一屏有三把全场尺加一把榜内尺，分母各不相同：母体榜（符合判据 '
                                      f'{matched} 只取前 {lim}）、当日综合分全场名次（{Q._int(counts.get("oper_scored"))} '
                                      f'只有分）、当日倍量加权分全场名次（同批 {Q._int(counts.get("dbl_scored"))} 只有分）；'
                                      f'埋伏那一列是榜内名次（那天命中埋伏形态的 {Q._int(counts.get("ambush_rows"))} 只里排），'
                                      '与全场名次不是同一把尺，不许混在一个列名里卖'},
                 'by': {'oper_rank': 'score.rank_in_market(oper_score)',
                        'dbl_rank': 'volume_double.dbl_energy（同场同批 energy_fp）',
                        'ambush_rank_in_board': 'ambush_setup.ambush_score（榜内，非全场）'}},
        'pct_rule': day_returns.RULE_NOTE,
        'indicator_counts': {'as_of': pd, **{k: Q._int(v) for k, v in (counts or {}).items()}},
        'rows': rows,
        'definitions': definitions,
        'coverage': Q._coverage(('score', 'volume_double', 'volume_breakout_signal',
                                 'ambush_setup', 'abnormal_move_daily', 'trend_stock_daily')),
        'caveats': caveats,
        'source': 'pg',
        'requests': 0,
    }
