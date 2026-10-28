# -*- coding: utf-8 -*-
"""节点分析（跨条件组合判据）：`analytics.board_node_daily`。

口径出自同事那份《涨停分析、板块分析、趋势股》三页说明
（`docs/涨停分析-板块分析-趋势股——from洪杰.md`）里「一、涨停分析 → 4. 节点分析」那一节。
本机的差别只有一处，但这一处是关键：**连板全部由本地日线自己数**（收盘 >= 涨停价 - 0.005 记封住），
不读东方财富涨停池 —— 所以这一格能回算全史、能对账、外部接口挂了也不瞎。

两条判据各自判断，同时命中写 `1, 2`：

  · **节点 1**（大盘砸下来那天，谁逆势封住第一板）：三件事同时成立
    —— ① 这一天这只票是**首板**；② 上证指数当日跌幅 >= 1 个点（涨跌幅 <= -1.0，
    读 `market_data.index_daily` 的 `000001.SH`）；③ 当日成交量 > 前一交易日成交量。
  · **节点 2**（高度被清空之后的第一天，谁敢接）：三件事同时成立
    —— ① 这一天是**首板或二板**（二板就是一进二）；② **前一交易日**全场最高连板高度 >= 6；
    ③ 站在那个最高高度的那批票**今天全部没有涨停**。第③条包含两种情况，都会让节点 2 成立：
    **断板**（今天有成交但收盘没封住）与**停牌**（上证已有今天这根日线，这只票却没有今天这行）。

  · **三板及以上不看当天**：改看这只票**本轮连板的首板日与二板日**，那两天仍按上面两条判，
    命中的编号取并集（见 `run_first_date` / `run_second_date`）。窗口把更早的连板截断、对不上时**不猜**，
    `hits` 留 NULL 并在 `hit_reason` 记 `run_window_broken`。

最高连板的样本先剔三类（与来源口径一致，理由也一致）：
  ① 当日 ST —— 用 `reference_data.security_st_period` 的**当日档期**；本机对它一条档期记录都没有的，
     才退回当前快照，退回的只数记在 `st_fallback_count`（这是本机与来源口径唯一的分岔：
     来源直接用今天的名称判 ST，那会把"现在是 ST、那年还不是"的票错当成 5% 档）；
  ② 新股 —— 名称「N/C + 汉字」开头，**或**截止这一天它的日线不足 6 根。6 根按**真条数**数
     （用每只票第 6 根日线的日期判），不拿场次跨度猜：按跨度猜会把上市头两周停过牌的票当成老票放进梯队；
  ③ 名称表里没有名字的票 —— 不能拿代码冒充名称，否则它会被当成非 ST 混进梯队。

缺项一律 NULL 不按 0：上证读不到、前一日没量、前一日没有梯队，都是**判不了**，
与"这一条不成立"必须在界面上分得开 —— 所以每行带 `hit_reason` 短码。

用法：
    python -m app.compute.board_node                        # 最新有日线的那一天
    python -m app.compute.board_node --date 2026-10-14
    python -m app.compute.board_node --recent 5
    python -m app.compute.board_node --since 2025-10-15 --skip-done --max-minutes 30
    python -m app.compute.board_node --dry-run

退出码：0 成功 / 1 没有可算的交易日 / 2 一趟都没写进去 / 3 有分段算失败 / 4 自限预算用完。
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.db import engine

SPEC_VERSION = 'node-1'

SH_INDEX = '000001.SH'          # 上证指数在本机的写法（index_daily 里这一条有 1,259 场，到最新交易日）
NODE1_SH_DROP = -1.0            # 节点 1 的上证门槛：当日涨跌幅 <= -1.0（个点）
NODE2_MIN_HEIGHT = 6            # 节点 2 要求前一交易日全场最高连板 >= 6
MIN_BARS_FOR_LADDER = 6         # 日线不足 6 根算新股，不进最高板样本
CHUNK_DAYS = 20
LOOKBACK_EXTRA = 40             # 分段多回看的场次：够一轮连板 + 前一交易日的梯队

# 封住涨停 = 收盘 >= 涨停价 - 0.005（涨停价的唯一写入口是 app/compute/limit_prices.py）。
# 除权形态那根（最低价涨破涨停价 / 最高价跌破跌停价）当成不可信，**直接截断连板**，不参与数。
SEAL = "d.close >= d.limit_up - 0.005"

_COLUMNS = ('trade_date', 'security_code', 'sealed', 'boards', 'run_first_date', 'run_second_date',
            'bars_used', 'sh_pct', 'vol_vs_prev', 'prev_volume_ok',
            'prev_max_boards', 'prev_top_count', 'prev_top_sealed', 'prev_top_halted', 'prev_top_broke',
            'node1', 'node2', 'hits', 'hit_reason', 'sh_row_missing', 'st_fallback_count', 'spec_version')
_PK = {'security_code', 'trade_date', 'spec_version'}
UPSERT = ("insert into analytics.board_node_daily ({cols}) values ({ph}) "
          "on conflict (security_code, trade_date, spec_version) do update set {upd}, "
          "calculated_at = now()")

# 窗口内每根日线先判封没封住，再按连续段分组数出"第几板"，最后只留封住的行。
# ⚠️ 连续段的分组写法（这一处吃过教训）：段键 = `rn - cum_seal`，而且**先筛掉没封板的行、再开窗**。
#    早先的写法是"非封板那天让组号 +1、然后在含非封板行的分区里数 row_number"，
#    结果**掉线那一天和下一段并进了同一组**：第几板整体偏 1（真首板被数成 2 板），
#    本轮首板日还会指到一根根本没封板的 K 线上。合成序列（T,F,T,T,F,T ⇒ 1,1,2,1）实测过才对。
# `min(...) over (partition by security_code, run_key)` 因此给出本轮首板日；二板日取段内 boards=2 那天。
# ⚠️ 这一段的 `bars_used` 是**窗口内**的根数，不是上市以来的根数，所以不往表里写
#    （写进去就是拿窗口读数冒充 lifetime 读数）；新股判定另有精确口径，见 `_sixth_bar_dates`。
CORE_SQL = """
WITH bars AS (
    SELECT d.security_code, d.trade_date, d.volume_shares, d.name, d.is_st,
           row_number() OVER (PARTITION BY d.security_code ORDER BY d.trade_date)             AS rn,
           lag(d.volume_shares) OVER (PARTITION BY d.security_code ORDER BY d.trade_date) AS vprev,
           lag(d.trade_date)    OVER (PARTITION BY d.security_code ORDER BY d.trade_date) AS prev_bar,
           CASE WHEN d.close IS NULL OR d.limit_up IS NULL THEN NULL
                WHEN d.low > d.limit_up OR (d.limit_down IS NOT NULL AND d.high < d.limit_down) THEN NULL
                WHEN {seal} THEN true ELSE false END                             AS sealed
      FROM (SELECT s2.security_code, s2.trade_date, s2.close, s2.high, s2.low, s2.limit_up,
                   s2.limit_down, s2.volume_shares, sec.name, sec.is_st
              FROM market_data.stock_daily s2
              JOIN reference_data.security sec USING (security_code)
             WHERE s2.trade_date >= %(w_from)s AND s2.trade_date <= %(d_hi)s
               AND sec.asset_type = 'stock') d
),
cum AS (
    SELECT bars.*,
           sum(CASE WHEN coalesce(sealed, false) THEN 1 ELSE 0 END)
               OVER (PARTITION BY security_code ORDER BY trade_date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)             AS cum_seal
      FROM bars
),
srows AS (
    SELECT cum.*,
           (rn - cum_seal)                                                        AS run_key,
           row_number()  OVER (PARTITION BY security_code, (rn - cum_seal) ORDER BY trade_date) AS boards,
           min(trade_date) OVER (PARTITION BY security_code, (rn - cum_seal))      AS run_first_date
      FROM cum
     WHERE coalesce(sealed, false)
),
runs AS (
    SELECT srows.*,
           min(CASE WHEN boards = 2 THEN trade_date END)
               OVER (PARTITION BY security_code, run_key)                          AS run_second_date
      FROM srows
)
SELECT security_code, trade_date, boards, run_first_date, run_second_date,
       volume_shares, vprev, prev_bar, name, is_st
  FROM runs
 ORDER BY security_code, trade_date
"""


def _sessions(lo: dt.date, hi: dt.date) -> List[dt.date]:
    return [r['trade_date'] for r in engine.query(
        'SELECT DISTINCT trade_date FROM market_data.stock_daily '
        ' WHERE trade_date BETWEEN %s AND %s ORDER BY 1', (lo, hi), role='reader') or []]


def _sessions_up_to(hi: dt.date, n: int) -> List[dt.date]:
    return sorted(r['trade_date'] for r in engine.query(
        'SELECT DISTINCT trade_date FROM market_data.stock_daily '
        ' WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT %s', (hi, n), role='reader') or [])


def _sh_daily(lo: dt.date, hi: dt.date) -> Dict[dt.date, Any]:
    rows = engine.query('SELECT trade_date, pct_change FROM market_data.index_daily '
                        ' WHERE security_code = %s AND trade_date >= %s AND trade_date <= %s ORDER BY 1',
                        (SH_INDEX, lo, hi), role='reader') or []
    return {r['trade_date']: r['pct_change'] for r in rows}


def _sixth_bar_dates() -> Dict[str, dt.date]:
    """每只票**第 6 根日线**是哪天（不足 6 根的票不在表里 ⇒ 永远算新股）。"""
    rows = engine.query('SELECT security_code, min(trade_date) AS d6 FROM ('
                        '  SELECT security_code, trade_date,'
                        '         row_number() OVER (PARTITION BY security_code ORDER BY trade_date) rn'
                        '    FROM market_data.stock_daily) z WHERE rn = %s GROUP BY 1',
                        (MIN_BARS_FOR_LADDER,), role='reader') or []
    return {r['security_code']: r['d6'] for r in rows}


def _st_periods() -> Tuple[Dict[str, List[Tuple[dt.date, dt.date]]], set]:
    """(ST 档期表, 有任何档期记录的代码集合)。"""
    rows = engine.query('SELECT security_code, valid_from, coalesce(valid_to, %s::date) AS valid_to'
                        '  FROM reference_data.security_st_period WHERE is_st',
                        ('9999-12-31',), role='reader') or []
    by: Dict[str, List[Tuple[dt.date, dt.date]]] = {}
    for r in rows:
        by.setdefault(r['security_code'], []).append((r['valid_from'], r['valid_to']))
    has = {r['security_code'] for r in engine.query(
        'SELECT DISTINCT security_code FROM reference_data.security_st_period', (), role='reader') or []}
    return by, has


def _name_is_new_stock(name: Optional[str]) -> bool:
    s = (name or '').strip()
    if len(s) < 2 or s[0] not in ('N', 'C'):
        return False
    return s[1].isalpha() and not ('A' <= s[1].upper() <= 'Z')


def _name_missing(name: Optional[str], code: str) -> bool:
    """名称表里没有真名字的票 —— 判梯队时必须剔掉，**不能拿代码冒充名称**。

    三种写法都算"没名字"：空、整串就是全码（`600519.SH`）、整串就是 6 位号段（`600519`）。
    后两种若当成有名字，它会被判成"非 ST 的老票"混进最高板梯队，节点 2 的分母就假了。
    """
    s = (name or '').strip()
    if not s:
        return True
    flat = s.replace('.', '').upper()
    return flat in {code.replace('.', '').upper(), code.split('.')[0].upper()}


def compute_window(d_lo: dt.date, d_hi: dt.date) -> List[Dict[str, Any]]:
    """算 [d_lo, d_hi] 这些目标日；窗口起点再往前 CHUNK_DAYS + LOOKBACK_EXTRA 根。"""
    need = CHUNK_DAYS + LOOKBACK_EXTRA + MIN_BARS_FOR_LADDER
    cal_all = _sessions_up_to(d_hi, need)
    if not cal_all or cal_all[-1] < d_lo:
        return []
    w_from = cal_all[0]
    sealed = engine.query(CORE_SQL.format(seal=SEAL), {'w_from': w_from, 'd_hi': d_hi}, role='reader') or []
    if not sealed:
        return []

    cal = _sessions(w_from, d_hi)                     # 本机日历（用行情自证，不猜）
    idx = {d: i for i, d in enumerate(cal)}
    sh = _sh_daily(w_from, d_hi)
    sixths = _sixth_bar_dates()
    st_by, st_has = _st_periods()

    # (code, date) -> 第几板：只含封住的行
    boards_at: Dict[Tuple[str, dt.date], int] = {}
    row_at: Dict[Tuple[str, dt.date], Dict[str, Any]] = {}
    by_date: Dict[dt.date, List[str]] = {}
    for r in sealed:
        k = (r['security_code'], r['trade_date'])
        boards_at[k] = int(r['boards'])
        row_at[k] = r
        by_date.setdefault(r['trade_date'], []).append(r['security_code'])

    def st_on(code: str, day: dt.date, cur_is_st: Any) -> Tuple[bool, bool]:
        """(当天算不算 ST, 是不是退回当前快照判的)。"""
        segs = st_by.get(code)
        if segs:
            return any(a <= day <= b for a, b in segs), False
        if code in st_has:
            return False, False        # 有档期记录、没有一条覆盖这天 ⇒ 确定不是 ST
        return bool(cur_is_st), True   # 本机对它一条档期记录都没有 —— 退回当前快照

    def in_ladder(code: str, day: dt.date) -> bool:
        r = row_at[(code, day)]
        name = r['name']
        if _name_missing(name, code) or _name_is_new_stock(name):
            return False
        d6 = sixths.get(code)
        if d6 is not None and day < d6:
            return False                                  # 截止这天还不足 6 根日线
        is_st, _ = st_on(code, day, r['is_st'])
        return not is_st

    # ── 逐场先算好"最高板梯队"（剔除后的口径）──
    ladder: Dict[dt.date, Dict[str, Any]] = {}
    for day, codes in by_date.items():
        kept = [c for c in codes if in_ladder(c, day)]
        fallback = sum(1 for c in kept
                       if st_on(c, day, row_at[(c, day)]['is_st'])[1])
        if not kept:
            ladder[day] = {'max': None, 'top': [], 'kept': 0, 'fallback': fallback}
            continue
        mx = max(boards_at[(c, day)] for c in kept)
        ladder[day] = {'max': mx, 'top': [c for c in kept if boards_at[(c, day)] == mx],
                       'kept': len(kept), 'fallback': fallback}

    # ── "前一日那批最高板今天还在不在板上"：断板 / 停牌 / 仍封 ──
    # 停牌要有证据：上证那一天有这根日线，而这只票没有 ⇒ 才算停牌；否则是"那一天还没到/整场没数据"。
    codes_needed = sorted({c for lad in ladder.values() for c in lad['top']})
    bar_dates: Dict[str, List[dt.date]] = {}
    if codes_needed:
        for r in engine.query('SELECT security_code, array_agg(trade_date ORDER BY trade_date) AS dts'
                              '  FROM market_data.stock_daily WHERE security_code = ANY(%s) GROUP BY 1',
                              (codes_needed,), role='reader') or []:
            bar_dates[r['security_code']] = r['dts']

    def status_next(code: str, day: dt.date) -> str:
        if (code, day) in boards_at:
            return 'sealed'
        dts = bar_dates.get(code) or []
        pos = bisect.bisect_left(dts, day)
        has_bar = pos < len(dts) and dts[pos] == day
        if has_bar:
            return 'broke'                                 # 当天有成交，收盘没封住
        return 'halted' if day in sh else 'unknown'        # 上证有当天这根线才谈得上停牌

    # ── 先给"首板日 / 二板日"那两天各算一次两条判据，三板及以上读的是这两天的并集 ──
    day_check: Dict[Tuple[str, dt.date], Dict[str, Any]] = {}
    for k, b in boards_at.items():
        if b not in (1, 2):
            continue
        code, day = k
        r = row_at[k]
        i = idx.get(day)
        prev_day = cal[i - 1] if i and i - 1 >= 0 else None
        lad_prev = ladder.get(prev_day) if prev_day else None
        top = (lad_prev or {}).get('top') or []
        states = [status_next(c, day) for c in top]
        sh_pct = sh.get(day)
        prev_ok = (r['prev_bar'] == prev_day) if prev_day else None
        vol_vs_prev = None
        if prev_ok and r['vprev'] and r['volume_shares'] is not None:
            vol_vs_prev = round(float(r['volume_shares']) / float(r['vprev']), 4)

        n1: Optional[bool] = None
        r1: List[str] = []
        if b == 1:
            if sh_pct is None:
                r1.append('n1:sh_pct_missing')
            elif float(sh_pct) > NODE1_SH_DROP:
                r1.append('n1:sh_pct_not_down')
            if not prev_ok:
                r1.append('n1:no_prev_volume')
            elif vol_vs_prev is None or vol_vs_prev <= 1:
                r1.append('n1:vol_not_up')
            n1 = (sh_pct is not None and float(sh_pct) <= NODE1_SH_DROP
                  and bool(prev_ok) and (vol_vs_prev or 0) > 1)
        else:
            r1.append('n1:first_board_only')

        n2: Optional[bool] = None
        r2: List[str] = []
        if not top:
            r2.append('n2:no_prev_top' if prev_day else 'n2:no_prev_day')
        elif (lad_prev or {}).get('max') is None or lad_prev['max'] < NODE2_MIN_HEIGHT:
            r2.append('n2:prev_top_below_6')
        elif 'sealed' in states:
            r2.append('n2:prev_top_still_sealed')
        elif 'unknown' in states:
            r2.append('n2:prev_top_state_unknown')
        else:
            n2 = True

        day_check[k] = {'node1': n1, 'node2': n2, 'reasons': r1 + r2,
                        'prev_max': (lad_prev or {}).get('max'), 'prev_top': len(top),
                        'prev_sealed': states.count('sealed'), 'prev_halted': states.count('halted'),
                        'prev_broke': states.count('broke'),
                        'sh_pct': sh_pct, 'sh_missing': sh_pct is None,
                        'vol_vs_prev': vol_vs_prev, 'prev_ok': prev_ok,
                        'fallback': (lad_prev or {}).get('fallback', 0)}

    out: List[Dict[str, Any]] = []
    for k, b in sorted(boards_at.items(), key=lambda x: (x[0][1], x[0][0])):
        code, day = k
        if not (d_lo <= day <= d_hi):
            continue
        lad_day = ladder.get(day) or {'max': None, 'top': [], 'kept': 0, 'fallback': 0}
        base = {'trade_date': day, 'security_code': code, 'sealed': True, 'boards': b,
                'run_first_date': row_at[k]['run_first_date'],
                'run_second_date': row_at[k]['run_second_date'],
                'bars_used': None,                      # 窗口内根数不等于上市以来根数，不写
                'sh_pct': sh.get(day), 'sh_row_missing': sh.get(day) is None,
                'st_fallback_count': lad_day['fallback'],
                'spec_version': SPEC_VERSION}
        ck = day_check.get(k)
        if ck is not None:
            base.update({'node1': ck['node1'], 'node2': ck['node2'],
                         'prev_max_boards': ck['prev_max'], 'prev_top_count': ck['prev_top'],
                         'prev_top_sealed': ck['prev_sealed'], 'prev_top_halted': ck['prev_halted'],
                         'prev_top_broke': ck['prev_broke'],
                         'vol_vs_prev': ck['vol_vs_prev'], 'prev_volume_ok': ck['prev_ok']})
            nums = [n for n, v in (('1', ck['node1']), ('2', ck['node2'])) if v]
            base['hits'] = ', '.join(nums) if nums else None
            base['hit_reason'] = None if nums else ','.join(ck['reasons'])
            out.append(base)
            continue

        # 三板及以上：这一格来自本轮首板日/二板日的并集，不是当天那两条
        f, s2 = base['run_first_date'], base['run_second_date']
        truncated = (f is None or (code, f) not in boards_at or boards_at[(code, f)] != 1)
        if truncated:
            base.update({'node1': None, 'node2': None, 'hits': None, 'hit_reason': 'run_window_broken',
                         'prev_max_boards': None, 'prev_top_count': 0, 'prev_top_sealed': 0,
                         'prev_top_halted': 0, 'prev_top_broke': 0,
                         'vol_vs_prev': None, 'prev_volume_ok': None})
            out.append(base)
            continue
        sub = day_check.get((code, f))
        sub2 = day_check.get((code, s2)) if s2 else None
        nums: List[str] = []
        if sub and sub['node1']:
            nums.append('1')
        if (sub and sub['node2']) or (sub2 and sub2['node2']):
            if '2' not in nums:
                nums.append('2')
        reasons: List[str] = []
        for s in (sub, sub2):
            if s and not (s['node1'] or s['node2']):
                reasons.extend(s['reasons'])
        base.update({'node1': None, 'node2': None,          # 当天不是首板/二板，这两条**不适用**，不是不成立
                     'prev_max_boards': None, 'prev_top_count': 0, 'prev_top_sealed': 0,
                     'prev_top_halted': 0, 'prev_top_broke': 0,
                     'vol_vs_prev': None, 'prev_volume_ok': None,
                     'hits': ', '.join(nums) if nums else None,
                     'hit_reason': None if nums else 'run_days_no_hit'})
        out.append(base)
    return out


def write(rows: Sequence[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    cols = ', '.join(_COLUMNS)
    ph = ', '.join(['%s'] * len(_COLUMNS))
    upd = ', '.join(f'{c} = excluded.{c}' for c in _COLUMNS if c not in _PK)
    payload = [tuple(r.get(c) for c in _COLUMNS) for r in rows]
    with engine.cursor(role='etl') as cur:
        cur.executemany(UPSERT.format(cols=cols, ph=ph, upd=upd), payload)
    return len(rows)


def verify() -> tuple[bool, list[str]]:
    """体检：判据自洽，不是"有没有数"。"""
    bad: list[str] = []
    agg = engine.query(
        'SELECT spec_version, count(*) n, '
        '       count(*) FILTER (WHERE boards = 1 AND node1 IS NULL) 首板但节点1没判, '
        '       count(*) FILTER (WHERE boards IN (1,2) AND node1 IS NULL AND node2 IS NULL '
        '                            AND hit_reason IS NULL) 一二板既无结论又无理由, '
        '       count(*) FILTER (WHERE hits IS NOT NULL AND hit_reason IS NOT NULL) 同时命中又同时有理由, '
        '       count(*) FILTER (WHERE hits = %s AND node2 IS NOT DISTINCT FROM false) 命中2却标不成立, '
        '       count(*) FILTER (WHERE prev_max_boards >= %s AND prev_top_count = 0) 说高度够6却说没人 '
        '  FROM analytics.board_node_daily GROUP BY 1',
        ('2', NODE2_MIN_HEIGHT), role='reader') or []
    for a in agg:
        print('  %s：行 %s／首板但节点1没判 %s／一二板既无结论又无理由 %s／同时命中又同时有理由 %s／'
              '命中2却标不成立 %s／梯队口径串了 %s'
              % (a['spec_version'], a['n'], a['首板但节点1没判'], a['一二板既无结论又无理由'],
                 a['同时命中又同时有理由'], a['命中2却标不成立'], a['说高度够6却说没人']))
        for k in ('首板但节点1没判', '一二板既无结论又无理由', '同时命中又同时有理由',
                  '命中2却标不成立', '说高度够6却说没人'):
            if int(a[k] or 0):
                bad.append('%s 有 %s 行 %s（判据不自洽）' % (a['spec_version'], a[k], k))
    if not agg:
        bad.append('一张行都没有 —— 步骤没跑，还是这一窗没有涨停？')
    return (not bad), bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='节点分析（组合判据）：analytics.board_node_daily')
    ap.add_argument('--date', help='只算这一天（YYYY-MM-DD）')
    ap.add_argument('--recent', type=int, default=1, help='往前数 N 个交易日')
    ap.add_argument('--since', help='从这一天起把所有交易日都算一遍（回算用）')
    ap.add_argument('--skip-done', action='store_true', help='跳过表里已有该 spec_version 的场')
    ap.add_argument('--max-minutes', type=float, help='自限预算，到点就停并交还剩几场')
    ap.add_argument('--dry-run', action='store_true', help='只算不写')
    ap.add_argument('--verify', action='store_true', help='写完后体检')
    a = ap.parse_args(argv)

    d_end = (dt.date.fromisoformat(a.date) if a.date else
             engine.scalar('SELECT max(trade_date) FROM market_data.stock_daily', role='reader'))
    if d_end is None:
        print('库里没有日线，谈不上节点')
        return 1
    targets = (_sessions(dt.date.fromisoformat(a.since), d_end) if a.since
               else _sessions_up_to(d_end, max(1, a.recent)))
    if a.skip_done and targets:
        done = {r['trade_date'] for r in engine.query(
            'SELECT DISTINCT trade_date FROM analytics.board_node_daily WHERE spec_version = %s',
            (SPEC_VERSION,), role='reader') or []}
        targets = [t for t in targets if t not in done]
    if not targets:
        print('没有要算的交易日（%s..%s，或这一批已算过）' % (d_end, SPEC_VERSION))
        return 1
    print('=== 节点分析（%s）：要算 %d 场 %s .. %s%s'
          % (SPEC_VERSION, len(targets), targets[0], targets[-1], '（dry-run）' if a.dry_run else ''))

    t0 = time.time()
    wrote = failed = 0
    for i in range(0, len(targets), CHUNK_DAYS):
        seg = targets[i:i + CHUNK_DAYS]
        if a.max_minutes and (time.time() - t0) / 60 > a.max_minutes:
            print('  ⏱ 自限预算 %s 分钟用完，还剩 %d 场没算（重跑带 --skip-done 接着算）'
                  % (a.max_minutes, len(targets) - i))
            return 4
        rows = compute_window(seg[0], seg[-1])
        hit1 = sum(1 for r in rows if '1' in (r['hits'] or ''))
        hit2 = sum(1 for r in rows if '2' in (r['hits'] or ''))
        broken = sum(1 for r in rows if r['hit_reason'] == 'run_window_broken')
        print('  %s .. %s：封板行 %d／命中节点1 %d 只次／命中节点2 %d 只次／连板窗口被截断 %d'
              % (seg[0], seg[-1], len(rows), hit1, hit2, broken))
        if a.dry_run:
            for r in [x for x in rows if x['hits']][:4]:
                print('    样本 %s %s 第 %s 板：hits=%s（前一日最高板 %s，那批 %s 只：'
                      '仍封 %s／断板 %s／停牌 %s；上证 %s）'
                      % (r['security_code'], r['trade_date'], r['boards'], r['hits'],
                         r['prev_max_boards'], r['prev_top_count'], r['prev_top_sealed'],
                         r['prev_top_broke'], r['prev_top_halted'], r['sh_pct']))
            continue
        if not rows:
            print('  ⚠ 这一段交出 0 行（这一窗没有封板行？先看 stock_daily 这段有没有数据）')
            failed += 1
            continue
        wrote += write(rows)

    if a.verify:
        ok, bad = verify()
        for b in bad:
            print('  ⚠ ' + b)
        print('  体检：%s' % ('通过' if ok else '不通过'))
        return 0 if ok else 2
    if failed:
        return 3
    return 0 if wrote or a.dry_run else 2


if __name__ == '__main__':
    raise SystemExit(main())
