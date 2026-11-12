# -*- coding: utf-8 -*-
"""「⑩ 明日展望」两张短名单的隔日表现：重算 → 落 `analytics.outlook_pick_daily` /
`analytics.outlook_baseline_daily`（策略回测那一屏读这两张表）。

2026-11-05 起（用户要求「往后 1、2、3 个交易日的涨跌，还有冲高率、整体高点数值涨幅」）：
每行除了隔日（第 1 日）开/高/收，还落**第 2/3 个交易日收盘**（`cl2_pct`/`cl3_pct`）、
**3 日内最高点涨幅**（`hi_max_3d_pct`＝"整体高点数值涨幅"）与**是否 3 日内冲过信号日收盘**
（`up3d`；汇总里的"冲高率"就是它的 true 占比）。第 2/3 日还没到的最近两场是 NULL（还没发生，
不是没涨）。口径细则在 `app/db/sql/64_outlook_backtest_d23.sql` 抬头。

为什么不在界面里现算：一场一场调选取函数要 8 分钟，页面不能等；更因为它**会被算错口径** ——
名单出自 `ingest/jobs/push/t10_tomorrow_outlook.py`，那个脚本还在人手里改。所以这里：

· **不重写选取判据**：直接 import 那一路的 `_hits_shortlist` / `_trend_shortlist`，
  两处各写一份就会出现"回溯的其实是另一个算法"（这一族的老毛病）。
· 每行一起存 `params`（当时的参数快照）与 `algo_stamp`（那个脚本的内容指纹）：
  屏上"这组胜率是哪版判据算的"当场读得出来，指纹与当前脚本不一致就出声。
· `spec_version` 在主键里：判据改版换版本号，旧行留着对照，不覆盖。

尺子（与 `duanxian/day_returns.py` 同一把，不另起）：涨幅一律用**后复权价**
`价 × coalesce(adjustment_factor,1)`，基准是信号日收价；隔日三个价各乘**当日**因子。
缺数一律 NULL + 在 `missing` 里点名，**不写 0**（0 会被读成"平盘"）。

    python -m app.compute.outlook_backtest --sessions 120            # 回填最近 120 场
    python -m app.compute.outlook_backtest --start 2026-06-02 --end 2026-11-04
    python -m app.compute.outlook_backtest --dry-run                 # 只算不写，打汇总
    python -m app.compute.outlook_backtest --show                    # 读库里已有行打汇总
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import statistics
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import engine                                          # noqa: E402
from app.intraday.store import _no_test_write                      # noqa: E402  同一道 pytest 闸，不抄第二份
from duanxian.day_returns import _num, _pct                        # noqa: E402  同一把尺子的收口件

SPEC_VERSION = 'outlook-pick-1'
CFG_FILE = ROOT / 'app' / 'intraday' / 'broadcast_config.json'
#: 判据住在哪两份文件（指纹把它们一起算，见 `algo_stamp()`）：推送那一炉 + 读侧那两份 SQL。
SOURCE_FILES = (ROOT / 'ingest' / 'jobs' / 'push' / 't10_tomorrow_outlook.py',
                ROOT / 'duanxian' / 'tomorrow_outlook.py')
#: 落进 params 的参数键（界面上要能说"这批行是按这几个数算的"）
PARAM_KEYS = ('t10_family', 't10_top_n', 't10_min_hits', 't10_window_sessions', 't10_hits_n',
              't10_trend_streak_min', 't10_trend_n', 't10_char_keep')


# ─────────────────────────────────────────────────────── 纯判据（不碰库，单测直接喂假数据）

def pair_stats(vals: list[Any]) -> dict | None:
    """一组涨幅（百分数）的读数。`None` 从**分子分母都剔掉**：停牌/没行的那笔不能算成 0%。

    交回 `{'n','mean','median','win_pct','ge3_pct','le3_pct'}`；一个值都没有 ⇒ **None**
    （不是全 0 的字典 —— 全 0 会被界面念成"这批票都不涨"）。
    """
    v = [x for x in (_num(y) for y in vals) if x is not None]
    if not v:
        return None
    n = len(v)
    return {'n': n,
            'mean': round(statistics.fmean(v), 4),
            'median': round(statistics.median(v), 4),
            'win_pct': round(sum(1 for x in v if x > 0) / n * 100, 2),
            'ge3_pct': round(sum(1 for x in v if x >= 3) / n * 100, 2),
            'le3_pct': round(sum(1 for x in v if x <= -3) / n * 100, 2)}


def delta(a: dict | None, b: dict | None, key: str = 'mean') -> float | None:
    """名单减基线（个百分点）。任何一边算不出 ⇒ None，**不许把"没数"当成 0 差**。"""
    if not a or not b or a.get(key) is None or b.get(key) is None:
        return None
    return round(a[key] - b[key], 4)


def gap_hfq(next_val: Any, base_hfq: Any) -> float | None:
    """隔日某价对信号日后复权收价的涨幅（百分数，两位）。"""
    return _pct(next_val, base_hfq)


def drift(stored: str | None, current: str | None) -> bool:
    """库里的脚本指纹与当前脚本不一致 ⇒ 口径已经漂过，旧行是"上一版算法"给的。"""
    return bool(stored and current and stored != current)


# ─────────────────────────────────────────────────────── 取数与写库

def cfg_params() -> dict:
    raw = json.loads(CFG_FILE.read_text(encoding='utf-8'))
    return {k: raw[k] for k in PARAM_KEYS if k in raw}


def algo_stamp() -> str:
    """判据来源的内容指纹（改了它＝口径变了，行里要留得下当时那一版）。

    ⚠️ 只哈希播报脚本是不够的：11-05 那轮把两张榜的 SQL 搬进了 `duanxian/tomorrow_outlook.py`
    （推送那一炉反过来 import 它），所以改那个文件才是改判据，而它不在 `SOURCE_FILE` 里 ——
    那样 `drift` 会永远报"没漂"，是一台不会响的报警器。这里把**判据真正住的那两份文件**一起哈希。
    """
    h = hashlib.sha256()
    for f in SOURCE_FILES:
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def _selectors():
    """现取那两路选取函数。**故意不在本文件复制判据**：那份住在播报脚本里，两处一份就会漂。"""
    from ingest.jobs.push import t10_tomorrow_outlook as src
    return src._hits_shortlist, src._trend_shortlist, src


def sessions_between(start: dt.date, end: dt.date) -> list[dt.date]:
    rows = engine.query(
        'SELECT distinct trade_date FROM market_data.stock_daily'
        ' WHERE trade_date between %s and %s ORDER BY trade_date', (start, end), role='reader') or []
    return [r['trade_date'] for r in rows]


def window_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """能算这一路的场次 = 两张输入表**都有**那天的场次。

    故意不按 `stock_daily` 的日历取：那会把"只有趋势榜有数、指标族还没落库"的日子也算进来，
    于是一边名单是空的、另一边是满的，汇总里两榜的样本窗口就不是一回事了。
    """
    rows = engine.query(
        'SELECT distinct i.trade_date FROM analytics.indicator_value i'
        '  WHERE i.trade_date between %s and %s'
        '    AND exists (SELECT 1 FROM analytics.trend_stock_daily t WHERE t.trade_date = i.trade_date)'
        '  ORDER BY i.trade_date', (start, end), role='reader') or []
    return [r['trade_date'] for r in rows]


def next_of(day: dt.date) -> dt.date | None:
    """全场次的下一场（不是这只票自己的下一根 —— 停牌不能把隔日拉长）。"""
    r = engine.query_one(
        'SELECT min(trade_date) AS n FROM market_data.stock_daily WHERE trade_date > %s',
        (day,), role='reader')
    return (r or {}).get('n')


def next_chain(day: dt.date, k: int = 3) -> list[dt.date | None]:
    """信号日后第 1..k 个交易日（一次查询）。尾巴没到就交 None —— 缺哪几天是哪几天的事，
    不能用"上一根 K 线"冒充（停牌会把隔日拉长，口径就漂了）。"""
    rows = engine.query(
        'SELECT distinct trade_date FROM market_data.stock_daily'
        ' WHERE trade_date > %s ORDER BY trade_date LIMIT %s',
        (day, k), role='reader') or []
    out = [r['trade_date'] for r in rows]
    while len(out) < k:
        out.append(None)
    return out


def forward_metrics(base_hfq: Any, days: list[tuple[Any, Any, Any] | None]) -> dict:
    """纯判据（不碰库，单测直接喂）：后复权基准价 + 往后最多 3 个交易日的 (开,高,收) 后复权三元组
    （按日序；某天没行＝停牌/还没到，给 None），算出全部涨幅列。

    返回键：`gap_pct`（第 1 日开）、`hi_pct`（第 1 日高）、`cl_pct`（第 1 日收）、
    `cl2_pct`/`cl3_pct`（第 2/3 日收）、`open_to_hi_pct`/`open_to_cl_pct`（第 1 日开盘买入口径）、
    `hi_max_3d_pct`（3 日最高取最大＝"整体高点数值涨幅"）、`up3d`（3 日内冲过信号日收盘；
    3 日全无行 ⇒ None＝判不了）、`missing`（哪几天没行的说明列表）。
    涨幅一律 `_pct`（百分数两位，缺任何一边 ⇒ None，不交 0）。
    """
    o1 = h1 = c1 = o2 = h2 = c2 = o3 = h3 = c3 = None
    missing: list[str] = []
    for i, d in enumerate(days, 1):
        if d is None:
            missing.append(f'd{i}：那一天它没有行（停牌/未上市，或那几天还没发生）')
            continue
        if i == 1:
            o1, h1, c1 = d
        elif i == 2:
            o2, h2, c2 = d
        else:
            o3, h3, c3 = d
    highs = [x for x in (h1, h2, h3) if x is not None]
    hi_max3 = max(highs) if highs else None
    hi_max3_pct = _pct(hi_max3, base_hfq)
    return {
        'gap_pct': _pct(o1, base_hfq),
        'hi_pct': _pct(h1, base_hfq),
        'cl_pct': _pct(c1, base_hfq),
        'cl2_pct': _pct(c2, base_hfq),
        'cl3_pct': _pct(c3, base_hfq),
        'open_to_hi_pct': _pct(h1, o1),
        'open_to_cl_pct': _pct(c1, o1),
        'hi_max_3d_pct': hi_max3_pct,
        'up3d': None if hi_max3_pct is None else bool(hi_max3_pct > 0),
        'missing': missing,
    }


def outcomes(day: dt.date, chain: list[dt.date | None], codes: list[str]) -> dict[str, dict]:
    """`{code: {base_hfq, base_close, days:[(开,高,收)后复权 或 None, …], raw_days:[原始价, 同形]}}`，
    days 与 `chain` 对齐。原始价只为对表（行情软件逐格能对上），判据与聚合只读后复权。

    信号日没有收价（基准价）的票**不在字典里**（缺基准谈不上"往后几天"）；
    基准在、往后某天没行的票在字典里，那一天是 None（`forward_metrics` 会在 missing 点名）。
    """
    dates = [d for d in chain if d is not None]
    if not codes or not dates:
        return {}
    pos = {d: i for i, d in enumerate(chain) if d is not None}
    rows = engine.query(
        """
        with b as (select security_code, (close * coalesce(adjustment_factor,1))::float hfq, close
                     from market_data.stock_daily where trade_date = %s and close is not null),
             x as (select security_code, trade_date,
                          open, high, close as xc,
                          (open  * coalesce(adjustment_factor,1))::float o,
                          (high  * coalesce(adjustment_factor,1))::float h,
                          (close * coalesce(adjustment_factor,1))::float c
                     from market_data.stock_daily where trade_date = any(%s::date[]) and close is not null)
        select b.security_code, b.hfq as base_hfq, b.close as base_close,
               x.trade_date, x.open, x.high, x.xc, x.o, x.h, x.c
          from b left join x on x.security_code = b.security_code
         where b.security_code = any(%s::text[])
        """, (day, dates, codes), role='reader') or []
    out: dict[str, dict] = {}
    for r in rows:
        e = out.setdefault(r['security_code'],
                           {'base_hfq': r['base_hfq'], 'base_close': r['base_close'],
                            'days': [None] * len(chain), 'raw_days': [None] * len(chain)})
        if r['trade_date'] is not None:
            e['days'][pos[r['trade_date']]] = (r['o'], r['h'], r['c'])
            e['raw_days'][pos[r['trade_date']]] = (r['open'], r['high'], r['xc'])
    return out


def baseline_for(day: dt.date, chain: list[dt.date | None]) -> dict | None:
    """同批场次的全市场等权基线（后复权，与名单同一把尺子）：开/高/收 + 第 2/3 日收
    + 3 日最高点涨幅（`hi_max_3d`）各自的均值与胜率（>0 占比）。缺哪一天哪一列就按
    自己的分母算（`filter (where … is not null)`），不拿别人的分母顶。

    固定 3 槽位：`chain` 里没到的那几天传 NULL，`filter (where trade_date = NULL)`
    恒不命中 ⇒ 那一列自然 NULL，SQL 不用按到场天数重拼。
    """
    dates = [d for d in chain if d is not None]
    if not dates:
        return None
    d1, d2, d3 = chain
    return engine.query_one(
        """
        with b as (select security_code, (close * coalesce(adjustment_factor,1))::float hfq
                     from market_data.stock_daily where trade_date = %s and close is not null),
             x as (select security_code, trade_date,
                          (open  * coalesce(adjustment_factor,1))::float o,
                          (high  * coalesce(adjustment_factor,1))::float h,
                          (close * coalesce(adjustment_factor,1))::float c
                     from market_data.stock_daily
                    where trade_date = any(array_remove(ARRAY[%s::date, %s::date, %s::date], NULL))
                      and close is not null),
             p as (select b.security_code, b.hfq,
                          max(x.o) filter (where x.trade_date = %s) o1,
                          max(x.h) filter (where x.trade_date = %s) h1,
                          max(x.c) filter (where x.trade_date = %s) c1,
                          max(x.c) filter (where x.trade_date = %s) c2,
                          max(x.c) filter (where x.trade_date = %s) c3,
                          greatest(max(x.h) filter (where x.trade_date = %s),
                                  max(x.h) filter (where x.trade_date = %s),
                                  max(x.h) filter (where x.trade_date = %s)) hm3
                     from b join x on x.security_code = b.security_code
                    group by b.security_code, b.hfq)
        select count(*) n,
               avg(o1/hfq-1)*100 gap, avg(h1/hfq-1)*100 hi, avg(c1/hfq-1)*100 cl,
               sum((o1/hfq-1 > 0)::int)::float/count(*)*100 gap_win,
               sum((h1/hfq-1 > 0)::int)::float/count(*)*100 hi_win,
               sum((c1/hfq-1 > 0)::int)::float/count(*)*100 cl_win,
               avg(c2/hfq-1) filter (where c2 is not null)*100 cl2,
               100.0*sum((c2/hfq-1 > 0)::int) filter (where c2 is not null)
                 /nullif(count(*) filter (where c2 is not null), 0) cl2_win,
               avg(c3/hfq-1) filter (where c3 is not null)*100 cl3,
               100.0*sum((c3/hfq-1 > 0)::int) filter (where c3 is not null)
                 /nullif(count(*) filter (where c3 is not null), 0) cl3_win,
               avg(hm3/hfq-1) filter (where hm3 is not null)*100 hi_max_3d,
               100.0*sum((hm3/hfq-1 > 0)::int) filter (where hm3 is not null)
                 /nullif(count(*) filter (where hm3 is not null), 0) hi_win_3d
          from p
        """, (day, d1, d2, d3, d1, d1, d1, d2, d3, d1, d2, d3),
        role='reader') or None


def picks_for_day(day: dt.date, cfg: dict) -> list[dict]:
    """那一天两张名单（原样按当时那一栏的排序给名次）。选取函数交 `None`＝那场缺数，不是空名单。"""
    hits_fn, trend_fn, _ = _selectors()
    out: list[dict] = []
    hits, _hl = hits_fn(day, cfg)
    trend, _tl = trend_fn(day, cfg)
    for section, lst in (('hits', hits or []), ('trend', trend or [])):
        for i, r in enumerate(lst, 1):
            out.append({'section': section, 'rank_in_section': i,
                        'security_code': r['security_code'],
                        'security_name': r.get('name'), 'board': r.get('board'),
                        'hits_today': r.get('hits_today'), 'days_in_top': r.get('days_in_top'),
                        'ma5_streak': r.get('ma5_streak'), 'char_level': r.get('char_level')})
    both = {p['security_code'] for p in out if p['section'] == 'hits'} & \
           {p['security_code'] for p in out if p['section'] == 'trend'}
    for p in out:
        p['in_both'] = p['security_code'] in both
    return out


_UPSERT_PICK = """
insert into analytics.outlook_pick_daily
 (trade_date, next_date, spec_version, section, security_code, security_name, board,
  rank_in_section, hits_today, days_in_top, ma5_streak, char_level, in_both,
  base_close, base_close_hfq, next_open, next_high, next_close,
  gap_pct, hi_pct, cl_pct, open_to_hi_pct, open_to_cl_pct,
  next2_date, next3_date, cl2_pct, cl3_pct, hi_max_3d_pct, up3d,
  missing, params, algo_stamp, calculated_at)
values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
        %s,%s,%s,%s,%s,%s,%s,%s,%s,now())
on conflict (trade_date, spec_version, section, security_code) do update set
  next_date = excluded.next_date, security_name = excluded.security_name,
  board = excluded.board, rank_in_section = excluded.rank_in_section,
  hits_today = excluded.hits_today, days_in_top = excluded.days_in_top,
  ma5_streak = excluded.ma5_streak, char_level = excluded.char_level,
  in_both = excluded.in_both, base_close = excluded.base_close,
  base_close_hfq = excluded.base_close_hfq, next_open = excluded.next_open,
  next_high = excluded.next_high, next_close = excluded.next_close,
  gap_pct = excluded.gap_pct, hi_pct = excluded.hi_pct, cl_pct = excluded.cl_pct,
  open_to_hi_pct = excluded.open_to_hi_pct, open_to_cl_pct = excluded.open_to_cl_pct,
  next2_date = excluded.next2_date, next3_date = excluded.next3_date,
  cl2_pct = excluded.cl2_pct, cl3_pct = excluded.cl3_pct,
  hi_max_3d_pct = excluded.hi_max_3d_pct, up3d = excluded.up3d,
  missing = excluded.missing, params = excluded.params, algo_stamp = excluded.algo_stamp,
  calculated_at = now()
"""

_UPSERT_BASE = """
insert into analytics.outlook_baseline_daily
 (trade_date, next_date, spec_version, n_stocks, gap_pct, hi_pct, cl_pct,
  gap_win_pct, hi_win_pct, cl_win_pct,
  next2_date, next3_date, cl2_pct, cl3_pct, hi_max_3d_pct, hi_win_3d_pct,
  cl2_win_pct, cl3_win_pct, calculated_at)
values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
on conflict (trade_date, spec_version) do update set
  next_date = excluded.next_date, n_stocks = excluded.n_stocks,
  gap_pct = excluded.gap_pct, hi_pct = excluded.hi_pct, cl_pct = excluded.cl_pct,
  gap_win_pct = excluded.gap_win_pct, hi_win_pct = excluded.hi_win_pct,
  cl_win_pct = excluded.cl_win_pct,
  next2_date = excluded.next2_date, next3_date = excluded.next3_date,
  cl2_pct = excluded.cl2_pct, cl3_pct = excluded.cl3_pct,
  hi_max_3d_pct = excluded.hi_max_3d_pct, hi_win_3d_pct = excluded.hi_win_3d_pct,
  cl2_win_pct = excluded.cl2_win_pct, cl3_win_pct = excluded.cl3_win_pct,
  calculated_at = now()
"""


#: 行字典的键序＝写库的列序（一处定死，`summarize` 与 `_UPSERT_PICK` 都从这里取，
#: 不在两处各数一遍下标 —— 数错一位就把"涨幅"念成"名次"）。
PICK_COLUMNS = ('trade_date', 'next_date', 'spec_version', 'section', 'security_code',
                'security_name', 'board', 'rank_in_section', 'hits_today', 'days_in_top',
                'ma5_streak', 'char_level', 'in_both', 'base_close', 'base_close_hfq',
                'next_open', 'next_high', 'next_close', 'gap_pct', 'hi_pct', 'cl_pct',
                'open_to_hi_pct', 'open_to_cl_pct',
                'next2_date', 'next3_date', 'cl2_pct', 'cl3_pct', 'hi_max_3d_pct', 'up3d',
                'missing', 'params', 'algo_stamp')
#: 末尾的 `calculated_at` 由 SQL 里的 `now()` 给，不占参数位 —— 所以这里比的是"参数位数"。
assert len(PICK_COLUMNS) == _UPSERT_PICK.count('%s'), \
    'PICK_COLUMNS 与 _UPSERT_PICK 的占位符数不一致（加列要两处一起改）'


def to_tuple(row: dict) -> tuple:
    return tuple(row.get(c) for c in PICK_COLUMNS)


def build_day(day: dt.date, cfg: dict, stamp: str) -> tuple[list[dict], tuple | None]:
    """一场：算出要写的行（纯算，不写库）。往后哪一天没行，那一天各列留 NULL 并在
    `missing` 里按 `d1_row`/`d2_row`/`d3_row` 点名（"还没发生"与"停牌"是同一格，界面上
    看窗口末端自然读得出来）。"""
    picks = picks_for_day(day, cfg)
    chain = next_chain(day, 3)
    nd, nd2, nd3 = chain
    om = outcomes(day, chain, sorted({p['security_code'] for p in picks}))
    params = json.dumps(cfg, ensure_ascii=False)
    rows: list[dict] = []
    for p in picks:
        o = om.get(p['security_code']) or {}
        base = o.get('base_hfq')
        if o:
            m = forward_metrics(base, o['days'])
            missing = {f'd{i}_row': note for note in m['missing']
                       for i in (1, 2, 3) if note.startswith(f'd{i}：')}
            raw1 = o.get('raw_days', [None, None, None])[0]
        else:
            missing = {f'd{i}_row': '信号日没有收价（基准价），三个涨幅判不了'
                       for i in (1, 2, 3)}
            m, raw1 = {}, None
        rows.append({
            'trade_date': day, 'next_date': nd, 'spec_version': SPEC_VERSION,
            'section': p['section'], 'security_code': p['security_code'],
            'security_name': p['security_name'], 'board': p['board'],
            'rank_in_section': p['rank_in_section'], 'hits_today': p['hits_today'],
            'days_in_top': p['days_in_top'], 'ma5_streak': p['ma5_streak'],
            'char_level': p['char_level'], 'in_both': p['in_both'],
            'base_close': o.get('base_close'), 'base_close_hfq': base,
            'next_open': _num(raw1[0]) if raw1 else None,
            'next_high': _num(raw1[1]) if raw1 else None,
            'next_close': _num(raw1[2]) if raw1 else None,
            'gap_pct': m.get('gap_pct'), 'hi_pct': m.get('hi_pct'), 'cl_pct': m.get('cl_pct'),
            'open_to_hi_pct': m.get('open_to_hi_pct'), 'open_to_cl_pct': m.get('open_to_cl_pct'),
            'next2_date': nd2, 'next3_date': nd3,
            'cl2_pct': m.get('cl2_pct'), 'cl3_pct': m.get('cl3_pct'),
            'hi_max_3d_pct': m.get('hi_max_3d_pct'), 'up3d': m.get('up3d'),
            'missing': json.dumps(missing, ensure_ascii=False) if missing else None,
            'params': params, 'algo_stamp': stamp})
    b = baseline_for(day, chain)
    brow = None
    if b:
        brow = (day, nd, SPEC_VERSION, int(b['n']),
                round(_num(b['gap']) or 0, 4), round(_num(b['hi']) or 0, 4),
                round(_num(b['cl']) or 0, 4), round(_num(b['gap_win']) or 0, 4),
                round(_num(b['hi_win']) or 0, 4), round(_num(b['cl_win']) or 0, 4),
                nd2, nd3,
                _num(b['cl2']), _num(b['cl3']), _num(b['hi_max_3d']), _num(b['hi_win_3d']),
                _num(b['cl2_win']), _num(b['cl3_win']))
    return rows, brow


def write_days(all_rows: list[dict], base_rows: list[tuple], days: list[dt.date]) -> dict:
    """重算一场先清这一场这一版（名单变短留下的旧行会被念成"那天的名单里有它"，那是假历史）。"""
    _no_test_write('outlook_pick_daily')
    with engine.conn('etl') as c:
        with c.cursor() as cur:
            cur.execute('DELETE FROM analytics.outlook_pick_daily'
                        ' WHERE trade_date = any(%s::date[]) AND spec_version = %s',
                        (days, SPEC_VERSION))
            cleared = cur.rowcount
            if all_rows:
                cur.executemany(_UPSERT_PICK, [to_tuple(r) for r in all_rows])
            cur.execute('DELETE FROM analytics.outlook_baseline_daily'
                        ' WHERE trade_date = any(%s::date[]) AND spec_version = %s',
                        (days, SPEC_VERSION))
            if base_rows:
                cur.executemany(_UPSERT_BASE, base_rows)
    return {'cleared': cleared, 'picks': len(all_rows), 'baseline': len(base_rows)}


def collect(start: dt.date, end: dt.date) -> dict:
    cfg = cfg_params()
    stamp = algo_stamp()
    days = window_days(start, end)
    rows: list[dict] = []
    brows: list[tuple] = []
    empty: list[dt.date] = []
    for d in days:
        r, b = build_day(d, cfg, stamp)
        if not r:
            empty.append(d)          # 那天两张名单都点不出票（或输入缺）：不进样本，也不配基线
            continue
        rows.extend(r)
        if b:
            brows.append(b)
    return {'days': [d for d in days if d not in set(empty)], 'empty_days': empty,
            'rows': rows, 'base_rows': brows, 'cfg': cfg, 'stamp': stamp}


def dedupe_by_stock(rows: list[dict]) -> list[dict]:
    """`both`（两榜都进）是一个**场次×票**的集合：同一只票同一天在两张榜各占一行，
    直接数会把一只票算成两只（`n` 翻倍；均值与分位数不变，所以这种错看不出来）。"""
    seen: set = set()
    out: list[dict] = []
    for r in rows:
        key = (str(r.get('trade_date')), str(r.get('security_code')))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def summarize(rows: list[dict]) -> dict:
    """按榜别汇总（读的是写进去前算好的百分数；视图那边从库里读同名列，走同一个 `pair_stats`，
    且 `both` 那一桶同样按场次×票去重 —— 两处口径不一致，屏上与 CLI 就会报两个数）。

    `up3d_pct` 单独算：`up3d` 是布尔（3 日内冲过信号日收盘），None＝判不了（从分子分母都剔），
    `mean` 就是 true 占比的百分数 —— 屏上那句"冲高率"。
    """
    out: dict[str, dict] = {}
    groups = (('hits', lambda r: r['section'] == 'hits'),
              ('trend', lambda r: r['section'] == 'trend'),
              ('both', lambda r: bool(r['in_both'])))
    for name, pred in groups:
        sel = [r for r in rows if pred(r)]
        if name == 'both':
            sel = dedupe_by_stock(sel)
        out[name] = {col: pair_stats([r[col] for r in sel])
                     for col in ('gap_pct', 'hi_pct', 'cl_pct', 'open_to_hi_pct', 'open_to_cl_pct',
                                 'cl2_pct', 'cl3_pct', 'hi_max_3d_pct')}
        uv = [r['up3d'] for r in sel if r.get('up3d') is not None]
        out[name]['up3d_pct'] = None if not uv else {
            'n': len(uv), 'mean': round(sum(1 for x in uv if x) * 100.0 / len(uv), 2)}
        out[name]['n'] = len(sel)
    return out


def run(start: dt.date | None = None, end: dt.date | None = None,
        sessions: int = 120, dry_run: bool = False) -> dict:
    all_days = window_days(dt.date(2024, 1, 1), end or dt.date.today())
    picked = [d for d in all_days if d >= start] if start else all_days[-sessions:] if sessions else all_days
    # 最后一场还没有"隔日"，算它只会得到一屏 NULL；留到明天再算
    if picked and next_of(picked[-1]) is None:
        picked = picked[:-1]
    data = collect(picked[0], picked[-1]) if picked else {'rows': [], 'base_rows': [], 'days': [],
                                                          'empty_days': []}
    summary = summarize(data['rows'])
    written = {'cleared': 0, 'picks': 0, 'baseline': 0}
    if picked and not dry_run:
        written = write_days(data['rows'], data['base_rows'], picked)
    return {'sessions': len(picked), 'sample_days': len(data['days']),
            'empty_days': [str(d) for d in data['empty_days']],
            'from': picked[0] if picked else None,
            'to': picked[-1] if picked else None, 'stamp': algo_stamp(),
            'params': cfg_params(), 'written': written, 'summary': summary}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='⑩ 明日展望名单的隔日表现回溯（落 analytics 两张表）')
    ap.add_argument('--start', default='', help='起始场次 YYYY-MM-DD（不给就按 --sessions 往回数）')
    ap.add_argument('--end', default='', help='结束场次 YYYY-MM-DD（默认今天）')
    ap.add_argument('--sessions', type=int, default=120, help='往回数多少场（默认 120）')
    ap.add_argument('--dry-run', action='store_true', help='只算与打汇总，不写库')
    a = ap.parse_args(argv)
    res = run(start=dt.date.fromisoformat(a.start) if a.start else None,
              end=dt.date.fromisoformat(a.end) if a.end else None,
              sessions=a.sessions, dry_run=a.dry_run)
    print(f'场次 {res["from"]} ~ {res["to"]}（{res["sessions"]} 场）· 脚本指纹 {res["stamp"]}')
    print('写入：', res['written'])
    for section, s in res['summary'].items():
        print(f'\n【{section}】{s["n"]} 笔')
        for k in ('gap_pct', 'hi_pct', 'cl_pct', 'cl2_pct', 'cl3_pct', 'hi_max_3d_pct'):
            st = s[k]
            if st:
                print(f'  {k}：均 {st["mean"]:+.2f}% 中位 {st["median"]:+.2f}% '
                      f'胜率 {st["win_pct"]:.1f}% ≥+3% {st["ge3_pct"]:.1f}% ≤-3% {st["le3_pct"]:.1f}%'
                      f'（有效 {st["n"]}）')
            else:
                print(f'  {k}：算不出（这一族一个值都没有 —— 不会当成 0）')
        up = s.get('up3d_pct')
        if up:
            print(f'  冲高率（3 日内冲过信号日收盘）：{up["mean"]:.1f}%（可判 {up["n"]} 笔）')
        else:
            print('  冲高率：算不出（3 日内高点一个都没有）')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
