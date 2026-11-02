# -*- coding: utf-8 -*-
"""读侧：首板四层过滤深析（用户 2026-10-29 给的那套四层方法论，做在短线复盘最下方）。

四层分别是「涨停逻辑与题材属性 / 盘口强度与量价结构 / 资金结构与龙虎榜 / 市场环境与情绪周期」。
**这一层只做读数与点名，不做合成评分**：外部那套资料给的阈值（封单比 1%/3%、量能 1.5~3 倍、
换手 10~20%、板块 ≥2-3 只同步涨停、买一/卖一 1.5 倍）是别人账户上的经验数，
本机分位实测下来有两处直接不成立（见 `BOUNDS` 里那两条 `本机事实`），照抄上屏就是把
判据换成一套没有本地依据的先验 —— 所以每一格都同时交「外部阈值」与「本机分位」两个数，
让读者自己看差多远，页面不给一个总分的幻觉。

三条数据现实（2026-10-30 现查 120 场，脚本 `.local/r5_measure*.py`，别再假设）：
1. **源池那一族只有 28% 有行**：`first_seal_at`/`reopen_count`/`at_limit_minutes` 27.6%、
   `order_amount`/`reason` 28.7%（北证与部分 ST 池子不给）。⇒ 这些列只能"有才判"，
   当筛子会把七成样本按"源站没采"筛掉，那与"它不强"是两回事。
2. **概念口径下「≥2-3 只同步涨停」零区分力**：本机概念很宽（`融资融券` 全市场 3,872 只成员），
   同一概念当日涨停只数 p10 就有 13 只、≥3 只覆盖 99.8% 的首板 ⇒ 这条外部判据被数据否掉，
   换成「该概念今日涨停只数 ÷ 它自己近 20 场中位」（本机 p50 1.67、≥2 倍 45%）才有高下。
3. **龙虎榜只有前五席位合计，没有席位明细**（`market_data.dragon_tiger_*` 实测 0 个 seat 列）
   ⇒ 「买一 vs 卖一」「知名游资」「拉萨天团」那一族本机做不出，界面明说不做，不拿合计数冒充。
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from duanxian import pg

POOL = 'analytics.limit_up_pool_daily'
SPEC = 'lup-1'
VR_SPEC = 'vr-1'
CONCEPT_LOOKBACK = 20        # 概念常态用近 20 场中位（不含当日）
WINDOW_SESSIONS = 60         # 取数窗口：够算 20 场常态 + 一点余量
MIN_BASE = 5                 # 概念常态的分母少于 5 场就不给倍数（第一档没历史就没"抬升"可言）

#: 每一格的口径：`label` 是列头，`external` 是资料给的外部阈值（原样摆，不冒充本机判据），
#: `local` 是 2026-10-30 现查 120 场的本机分位（判"高下"用这把，判"合不合格"用那把）。
BOUNDS: tuple[dict, ...] = (
    {'key': 'first_seal', 'label': '首封时刻', 'unit': 'HH:MM', 'layer': '盘口',
     'external': '越早越强（资料：开盘即板最强、午前板次之、尾盘板最差）',
     'local': '本机（有源池行的那 27.6% 里）≤09:35 占 16.2%、≤10:00 占 42.9%、≥14:00 占 10.7%',
     'hi': '早'},
    {'key': 'seal_ratio', 'label': '封单额 ÷ 流通市值', 'unit': '%', 'layer': '盘口',
     'external': '资料：>1% 算强、>3% 算强势',
     'local': '本机 p50 0.86%、>1% 占 42.1%、>3% 只占 5.4% ⇒ 那两个数在本机是 p70 与 p95，不是「及格线」',
     'hi': '大'},
    {'key': 'reopen', 'label': '炸板次数', 'unit': '次', 'layer': '盘口',
     'external': '资料：0 次封得死，≥2 次算反复',
     'local': '本机 0 次占 68.7%、≥2 次占 12.4%、p95＝3 次',
     'hi': '小'},
    {'key': 'vol_mult', 'label': '量能倍数（当日量 ÷ 上一根日线量）', 'unit': '倍', 'layer': '盘口',
     'external': '资料：1.5~3 倍健康',
     'local': '本机 ∈[1.5,3] 只占 32.3%、≥3 倍 11.7%、<1 倍 28.0%（vr-1 那把尺，与倍量榜同一条）',
     'hi': '区间'},
    {'key': 'turnover', 'label': '换手率', 'unit': '%', 'layer': '盘口',
     'external': '资料：10~20% 健康、>40~50% 死亡换手',
     'local': '本机 p50 5.9%、≥10% 占 25.3%、≥20% 占 4.9%、≥40% 只占 0.4% ⇒ 那两档在本机是极端值，'
              '照抄会把九成五的首板判成「不健康」，所以只摆数不给判',
     'hi': '区间'},
    {'key': 'theme_lift', 'label': '所属概念的当日涨停热度抬升', 'unit': '倍', 'layer': '题材',
     'external': '资料：板块内 ≥2-3 只同步涨停才算有联动',
     'local': '外部那条在本机零区分力（同概念当日涨停只数 p10 就有 13 只、≥3 只覆盖 99.8% 的首板）'
              '⇒ 换成「÷ 该概念近 20 场中位」：本机 p50 1.67、≥2 倍 45.0%',
     'hi': '大'},
    {'key': 'theme_fresh', 'label': '该概念距上一次上涨停榜隔几场', 'unit': '场', 'layer': '题材',
     'external': '资料：新题材（「新/大/近」）优于反复炒过的',
     'local': '本机 88.6% 的相邻两次上榜只隔 ≤2 场 ⇒ ≥5 场才读得出「隔了一阵没炒」，≥20 场只占 0.8%',
     'hi': '大'},
    {'key': 'dt_buy_sell', 'label': '龙虎榜前五合计 买入 ÷ 卖出', 'unit': '倍', 'layer': '资金',
     'external': '资料：买一 ≥ 卖一 1.5 倍（单席位）',
     'local': '本机没有席位明细，只有前五合计：≥1.5 倍占 50.2%、≥2 倍 29.3% ⇒ 这条只能到合计口径，'
              '与资料的「买一 vs 卖一」不是同一件事',
     'hi': '大'},
    {'key': 'inst_net', 'label': '机构席位净买', 'unit': '亿元', 'layer': '资金',
     'external': '资料：出现机构买入席位是加分项',
     'local': '首板样本里当日有机构行的占 12.9%（有机构行的里面买方席位数 ≥1 占 79.9%、'
              '净买 >0 占 56.9%）⇒ 没有机构行读作「没上榜/没机构」，不读作「没有资金」',
     'hi': '大'},
)
BOUND_BY_KEY = {b['key']: b for b in BOUNDS}

#: 本机拼不出来的那一族：宁可空着并说为什么，不拿相近的数顶上（那族老毛病）
CANNOT: tuple[dict, ...] = (
    {'what': '龙虎榜席位明细（买一 vs 卖一、知名游资席位、「拉萨天团」）',
     'why': '`market_data.dragon_tiger_detail` / `_institution` 实测 0 个席位名列（只有前五合计金额与'
            '机构席位数），本机没有席位身份数据 ⇒ 这一族整条不做，不用合计数冒充'},
    {'what': '分时「一波/两波封板」形状',
     'why': '要 1 分钟线。本机 `market_data.stock_minute_*` 只覆盖 2026-08-14 ~ 2026-10-26'
            '（且 2026-10-20 整场缺席），窗口外的场次根本算不了；而且「几波」的切法在本地没标定过'
            '（多少回落算一波）⇒ 不给判，只给已落库的「首封时刻 / 炸板次数 / 在板分钟数」'},
    {'what': '涨停逻辑的「驱动硬度」评级（业绩驱动 > 政策 > 题材 > 跟风）',
     'why': '本地只有东财给的一条涨停原因串与行业/概念归属，没有事件类别与因果标注 ⇒ '
            '只把原因串与概念原样列出，不给它打硬度分'},
    {'what': '情绪周期的「正向/逆向」定性',
     'why': '场次卡交的是晋级率、炸板率、最高板、涨跌家数比这些数与它们近 20 场的位置；'
            '「现在是正向还是逆向」是判断不是读数，交给看的人，不由本屏下结论'},
)

DEFINITIONS = {
    'parent': '母体＝本地库 `analytics.limit_up_pool_daily`（lup-1）当日封住且 `consec=1` 的那些票 —— '
              '与上面「涨停分析」那张表同一把尺，不另起一套；不含触板没封住的（那一族在首板形状那块里另算）。',
    'no_score': '这一屏没有合成总分。每层各摆各的读数，命中几条只写「命中 N 条」，'
                '因为四层的量纲互不相干（分钟、倍、%、只），加权出来的数没有读法，'
                '只会给一个精确的幻觉（同《分配者视角》那次「分数不可比」的教训）。',
    'missing': '缺数一律交 null（界面 —），不补 0、不判成「弱」。四族里源池那一族（首封/炸板/在板分钟/封单额）'
               '本机只有约 28% 的场次有行 —— 那是源站没给，不是「这几只票封得差」。',
    'concept': '概念归属读本地 `reference_data.sector_member`（板块类型=概念、成员未出、按概念名去重），'
               '与东财行业是两套归属体系，不能互证；归属表只有当前名单，不追溯历史成员 ⇒ '
               '早两年那场按今天的成员数出来的只数会偏。',
    'caliber': '两把尺并排给：一列是资料里那套外部阈值，一列是 2026-10-30 现查 120 场的本机分位。'
               '本机分位用来自认「这格在本机算高还是低」，外部阈值用来自查「我是不是在用别人的经验数」。',
}


def _num(v: Any, nd: int | None = None) -> Any:
    """出口清洗：`pd.NaT` 是 datetime 的实例，`isna` 的闸必须排在日期判断之前。"""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    import pandas
    try:
        if pandas.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (dt.date, dt.datetime)):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f or f in (float('inf'), float('-inf')):
        return None
    return round(f, nd) if nd is not None else f


def _rows(sql: str, params=()) -> list[dict]:
    df = pg.fetch_df(sql, params)
    return [] if df is None else [{k: _num(v) for k, v in row.items()} for row in df.to_dict('records')]


def _pctl(vals: list, q: float) -> float | None:
    v = sorted(float(x) for x in vals if isinstance(x, (int, float)) and math.isfinite(float(x)))
    if not v:
        return None
    if len(v) == 1:
        return round(v[0], 4)
    pos = q * (len(v) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (pos - lo), 4)


def _median(vals: list) -> float | None:
    return _pctl(vals, 0.5)


def _hhmm(v: Any) -> str | None:
    s = str(v or '')
    return s[:5] if len(s) >= 5 else (s or None)


def _minutes(v: Any) -> float | None:
    """'HH:MM' → 从 09:30 起算的分钟数（11:30~13:00 午休那段不计，只用于"早/晚"排序与分桶）。"""
    hm = _hhmm(v)
    if not hm or ':' not in hm:
        return None
    h, m = hm.split(':')
    mins = int(h) * 60 + int(m)
    am = max(0, min(mins, 690) - 570)                       # 09:30~11:30 → 0..120
    pm = max(0, mins - 780) if mins >= 780 else 0           # 13:00 起 → 再加
    return float(am + pm)


# ---------------------------------------------------------------- 取数

_SQL_SAMPLE = f"""
select p.trade_date::text as d, p.security_code as c, s.name, s.board, s.is_st,
       p.consec, p.pct_change, p.turnover_pct, p.amount_yuan,
       p.order_amount, p.float_cap_yuan, p.float_cap_basis,
       p.first_seal_at::text as first_seal, p.reopen_count, p.at_limit_minutes,
       p.reason, p.src_has_row, p.missing,
       v.vol_ratio
  from {POOL} p
  left join reference_data.security s on s.security_code = p.security_code
  left join analytics.volume_ratio_daily v
         on v.security_code = p.security_code and v.trade_date = p.trade_date
        and v.spec_version = '{VR_SPEC}'
 where p.spec_version = '{SPEC}' and p.sealed and p.consec = 1 and p.trade_date = %s::date
 order by p.security_code
"""

#: 概念 × 场次的涨停只数（窗口内一次取全，常态与新鲜度都从这份算）
_SQL_CONCEPT = f"""
with pool as (
    select trade_date::text as d, security_code as c
      from {POOL}
     where spec_version = '{SPEC}' and sealed
       and trade_date > (select (select max(trade_date) from {POOL}
                                  where spec_version = '{SPEC}' and sealed and trade_date <= %s::date)
                           - (%s * 2.2 * interval '1 day'))
       and trade_date <= %s::date),
member as (
    select distinct i.name, left(m.security_code, 6) as c6
      from reference_data.sector_member m
      join reference_data.sector_info i using (sector_code)
     where i.sector_type = '概念'
       and (m.out_date is null or m.out_date > current_date))
select p.d, p.c, m.name, count(*) over (partition by p.d, m.name)::int as n
  from pool p join member m on m.c6 = left(p.c, 6)
"""

_SQL_BREADTH = """
select trade_date::text as d, promotion_rate, broken_rate, highest_board, up_ratio,
       median_pct, limit_up_count, limit_down_count, prev_limit_up
  from analytics.market_breadth
 where trade_date <= %s::date
 order by trade_date desc limit %s
"""

#: 昨日涨停股今日平均溢价（母体＝前一场的封住涨停，涨幅走日线，不用涨停池 —— 池里只有当日涨停的行）
_SQL_PREMIUM = f"""
with prev as (
    select security_code as c
      from {POOL}
     where spec_version = '{SPEC}' and sealed and trade_date = (
             select max(trade_date) from {POOL}
              where spec_version = '{SPEC}' and sealed and trade_date < %s::date))
select avg(sd.pct_change)::numeric(10,4) as avg_pct, count(*)::int as n
  from prev join market_data.stock_daily sd on sd.security_code = prev.c and sd.trade_date = %s::date
"""


def _concept_views(concept_rows: list[dict], day: str) -> dict:
    """概念 → (当日只数 ÷ 近 20 场中位) 与"距上一次上榜隔几场"。

    常态只用**当日之前**的场次（当日自己不能抬自己），少于 `MIN_BASE` 场就不给倍数。
    """
    by_name: dict[str, list[tuple[str, int]]] = {}
    for r in concept_rows:
        by_name.setdefault(r['name'], []).append((r['d'], r['n']))
    lift: dict[str, dict] = {}
    for name, pairs in by_name.items():
        pairs = sorted(set(pairs))
        today = next((n for d, n in pairs if d == day), None)
        if today is None:
            continue
        before = [n for d, n in pairs if d < day]
        base = _median(before[-CONCEPT_LOOKBACK:]) if len(before) >= MIN_BASE else None
        hist = [d for d, _n in pairs if d < day]
        gap = None
        if hist:
            gap = len([d for d in _all_days if day > d > hist[-1]])
        lift[name] = {'concept': name, 'count': today,
                      'base_median': base,
                      'lift': (round(today / base, 3) if base else None),
                      'base_sessions': len(before[-CONCEPT_LOOKBACK:]) if before else 0,
                      'gap_sessions': (gap + 1) if gap is not None else None}
    return lift


_all_days: list[str] = []            # 由 panel() 填：算「隔几场」要的全场次序列（模块级只为免传参绕圈）


def _session_window(day: str) -> list[str]:
    rows = _rows(f"""select distinct trade_date::text as d from {POOL}
                      where spec_version = '{SPEC}' and sealed and trade_date <= %s::date
                      order by d desc limit %s""", (day, WINDOW_SESSIONS))
    return sorted(r['d'] for r in rows)


def panel(date: Any = '', limit: Any = 0) -> dict:
    """短线复盘页最下方那一块：所选交易日首板的四层读数 + 场次卡 + 覆盖率声明。"""
    global _all_days
    if not pg.enabled():
        return {'available': False,
                'reason': '本地库连不上（VIBE_PG / PG_USER / PG_PASSWORD 没配或没起来）—— '
                          '这是「库不通」，不是「今天没有首板」。'}
    days = _session_window(str(date or '').strip() or dt.date.today().isoformat())
    if not days:
        return {'available': False,
                'reason': f'`{POOL}`（spec `{SPEC}`）里一场封住的涨停都没有 ⇒ 加工链的涨停池那一步'
                          '还没跑过，先补 `python -m app.compute.limit_up_pool --start … --end …`。'}
    day = days[-1] if not str(date or '').strip() else str(date).strip()[:10]
    if day not in days and day > days[-1]:
        day = days[-1]
    _all_days = days
    win_from = days[0]

    sample = _rows(_SQL_SAMPLE, (day,))
    if not sample:
        return {'available': False, 'date': day,
                'reason': f'这一场（{day}）库里没有封住的首板行；最近一场有涨停的是 {days[-1]} —— '
                          '差的场次没算过，补 `python -m app.compute.limit_up_pool --start <YYYYMMDD> '
                          '--end <YYYYMMDD>`。'}

    concept_rows = _rows(_SQL_CONCEPT, (day, WINDOW_SESSIONS, day))
    per_stock: dict[str, list[dict]] = {}
    for r in concept_rows:
        if r['d'] == day:
            per_stock.setdefault(r['c'], []).append(r['name'])
    views = _concept_views(concept_rows, day)

    cap = max(0, int(limit or 0))
    rows = []
    for r in sample:
        names = per_stock.get(r['c']) or []
        best = None
        for nm in names:
            v = views.get(nm)
            if not v:
                continue
            score = v['lift'] if v['lift'] is not None else -1
            if best is None or score > (best.get('lift') if best.get('lift') is not None else -1):
                best = v
        cap_yuan = r.get('float_cap_yuan')
        order = r.get('order_amount')
        seal_ratio = (round(100.0 * float(order) / float(cap_yuan), 4)
                      if order and cap_yuan else None)
        rows.append({
            'code': r['c'], 'name': r.get('name'), 'board': r.get('board'), 'is_st': r.get('is_st'),
            'pct_change': _num(r.get('pct_change'), 2),
            # ① 题材
            'concept_top': (best or {}).get('concept'),
            'concept_count': (best or {}).get('count'),
            'concept_lift': (best or {}).get('lift'),
            'concept_gap': (best or {}).get('gap_sessions'),
            'concept_base': (best or {}).get('base_median'),
            'concept_all': names[:6], 'concept_n': len(names),
            'reason': r.get('reason'),
            # ② 盘口
            'first_seal': _hhmm(r.get('first_seal')), 'first_seal_min': _minutes(r.get('first_seal')),
            'reopen': r.get('reopen_count'), 'at_limit_minutes': r.get('at_limit_minutes'),
            'seal_ratio_pct': seal_ratio,
            'seal_basis': r.get('float_cap_basis'),
            'vol_mult': _num(r.get('vol_ratio'), 3),
            'turnover': _num(r.get('turnover_pct'), 3),
            # ③ 资金（在下面 join 进来）
            'src_row': bool(r.get('src_has_row')),
            'missing': r.get('missing'),
        })
    rows.sort(key=lambda x: (-(x.get('concept_lift') or 0), -(x.get('seal_ratio_pct') or 0)))

    dt_map = {}
    for r in _rows("""select security_code as c, trade_date::text as d, buy_amount, sell_amount,
                             net_buy, net_buy_ratio, reason
                        from market_data.dragon_tiger_detail
                       where trade_date = %s::date""", (day,)):
        dt_map.setdefault(r['c'], []).append(r)
    ti_map = {}
    for r in _rows("""select security_code as c, buy_institution_count, sell_institution_count,
                             institution_net
                        from market_data.dragon_tiger_institution where trade_date = %s::date""", (day,)):
        ti_map[r['c']] = r
    for r in rows:
        mine = dt_map.get(r['code']) or []
        buy = sum(float(x['buy_amount']) for x in mine if x.get('buy_amount'))
        sell = sum(float(x['sell_amount']) for x in mine if x.get('sell_amount'))
        ti = ti_map.get(r['code']) or {}
        r['on_dt'] = bool(mine)
        r['dt_lists'] = len(mine)
        r['dt_buy_sell'] = round(buy / sell, 3) if buy and sell else None
        r['dt_net_yi'] = (round(sum(float(x['net_buy']) for x in mine if x.get('net_buy')) / 1e8, 4)
                          if any(x.get('net_buy') for x in mine) else None)
        r['dt_net_ratio'] = _num(mine[0].get('net_buy_ratio'), 3) if mine else None
        r['inst_buy_count'] = ti.get('buy_institution_count')
        r['inst_sell_count'] = ti.get('sell_institution_count')
        r['inst_net_yi'] = (round(float(ti['institution_net']) / 1e8, 4)
                            if ti.get('institution_net') is not None else None)

    # ── ④ 场次卡（全场同一件事，不逐票重复）──────────────────────────────
    breadth = _rows(_SQL_BREADTH, (day, 40))
    today_b = next((b for b in breadth if b['d'] == day), None)
    near = breadth[:20]

    def dist(key: str) -> dict:
        vals = [b.get(key) for b in near if b.get(key) is not None]
        cur = _num(today_b.get(key), 3) if today_b else None
        below = sum(1 for v in vals if cur is not None and float(v) <= cur)
        return {'current': cur, 'p25': _pctl(vals, 0.25), 'median': _median(vals),
                'p75': _pctl(vals, 0.75), 'sessions': len(vals),
                'percentile_in_20': (round(100.0 * below / len(vals), 1) if vals and cur is not None else None)}

    session = {
        'date': day,
        'promotion_rate': dist('promotion_rate') if today_b else None,
        'broken_rate': dist('broken_rate') if today_b else None,
        'highest_board': dist('highest_board') if today_b else None,
        'up_ratio': dist('up_ratio') if today_b else None,
        'median_pct': dist('median_pct') if today_b else None,
        'limit_up_count': today_b.get('limit_up_count') if today_b else None,
        'limit_down_count': today_b.get('limit_down_count') if today_b else None,
        'breadth_source': 'analytics.market_breadth（d6 那一步，与首页那张宽度块同一个出口）',
        'premium': None, 'prev_day': None,
    }
    prev_day = _rows(f"""select max(trade_date)::text as d from {POOL}
                          where spec_version='{SPEC}' and sealed and trade_date < %s::date""", (day,))
    if prev_day and prev_day[0]['d']:
        pd = prev_day[0]['d']
        session['prev_day'] = pd
        pr = _rows(_SQL_PREMIUM, (day, day))
        if pr and pr[0].get('avg_pct') is not None:
            session['premium'] = {'avg_pct': _num(pr[0]['avg_pct'], 3), 'n': pr[0]['n'],
                                  'text': f'昨日（{pd}）封住的涨停股，今日平均涨幅 '
                                          f'{_num(pr[0]["avg_pct"], 2)}%（母体 {pr[0]["n"]} 只有日线算得出的那些）'}
        else:
            session['premium'] = {'avg_pct': None, 'n': 0,
                                  'text': f'昨日（{pd}）封住的涨停股今天日线还没落 ⇒ 溢价算不出，'
                                          '这一格读作"还没到"，不读作 0%'}

    # ── 覆盖率：把"判不了"与"弱"分开 ─────────────────────────────────────
    n = len(rows)

    def cov(key: str) -> dict:
        got = sum(1 for r in rows if r.get(key) is not None)
        return {'have': got, 'total': n, 'pct': (round(100.0 * got / n, 1) if n else None)}

    coverage = {'first_seal': cov('first_seal'), 'reopen': cov('reopen'),
                'at_limit_minutes': cov('at_limit_minutes'), 'seal_ratio_pct': cov('seal_ratio_pct'),
                'vol_mult': cov('vol_mult'), 'turnover': cov('turnover'),
                'concept_lift': cov('concept_lift'), 'on_dt': {'have': sum(1 for r in rows if r['on_dt']),
                                                               'total': n,
                                                               'pct': round(100.0 * sum(1 for r in rows if r['on_dt']) / n, 1) if n else None},
                'note': '源池那一族（首封/炸板/在板分钟/封单额）本机只有约 28% 的首板有行（北证与部分 ST '
                        '池子不给）⇒ 这些列不能当筛子：按它们筛等于按「源站采没采」筛。'}
    if cap:
        shown = rows[:cap]
    else:
        shown = rows

    return {
        'available': True, 'spec': SPEC, 'vr_spec': VR_SPEC, 'parent_spec': SPEC,
        'date': day, 'window_from': win_from, 'sessions': len(days),
        'parent': {'total': n, 'text': f'母体＝本地库 lup-1 当日封住且 consec=1 的首板 {n} 只'
                                       f'（与上面「涨停分析」那张表同一把尺；不含触板没封住的）'},
        'rows': shown, 'rows_shown': len(shown), 'rows_limit': cap or None,
        'layers': {'1': '题材与板块联动', '2': '盘口强度与量价结构', '3': '资金结构（龙虎榜）',
                   '4': '市场环境与情绪周期（场次卡，不逐票重复）'},
        'bounds': [dict(b) for b in BOUNDS],
        'cannot': [dict(c) for c in CANNOT],
        'definitions': dict(DEFINITIONS),
        'session': session,
        'coverage': coverage,
        'caveats': [
            {'code': 'no_composite',
             'text': DEFINITIONS['no_score']},
            {'code': 'external_thresholds',
             'text': DEFINITIONS['caliber']},
            {'code': 'concept_tie_useless',
             'text': '资料那条「板块内 ≥2-3 只同步涨停才算有联动」在本机概念口径下覆盖 99.8% 的首板'
                     '（同概念当日涨停只数 p10 就有 13 只）⇒ 这一条不参与判断，题材那格改用'
                     '"该概念今日涨停只数 ÷ 它自己近 20 场中位"。'},
            {'code': 'seat_detail_missing',
             'text': CANNOT[0]['why']},
        ],
        'generated_at': dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec='seconds'),
    }
