# -*- coding: utf-8 -*-
"""资金轮动趋势（需求：用户 2026-09-27「近 10 天连续流入、近 20/30 天都做统计并画趋势图」）。

一句话：**这里交的是统计量，不是信号。** 因为实测下来"连续吸金"这件事没有惯性 ——
份额连续 n 天抬升之后，第 n+1 天继续抬升的概率从 46%（n=1，等于基线）**单调掉到 31%**（n=9）；
复核脚本 `.local/flow_share_test.py`（1249 场 × 963 板块，按交易日分层）。
所以 `streak_up_days` 越大越该被读成"这一段快到了"，不是"资金在坚定进场"。
把它包装成看多信号是错的，数据侧只交出 streak / 累计 / 名次 + 原始输入，排榜与配色归界面。

三种度量各归各位：
  * `share`（默认，现在就能算）= 本板块成交额 ÷ 当日全场板块成交额合计。**只有份额口径才是"轮动"**
    —— 用绝对额测连续性会得到 73%→83% 的"延续"，那全是全场共同放量的自相关（实测，见 08 契约坑 12）。
  * `main_net` = 主力净流入额，来自 `market_data.fund_flow_rank`。**该表 0 行**（逐日入库未接上，
    链条里跑的 `c11_fundflow` 走的是已被掐的东财 push2），所以这个 basis 现在**一行都不写**，
    只如实返回 skipped 与缺多少场。等《数据获取》把 `c21_fundflow_sina` 接上调度、攒够场次再算。
  * `turnover`（2026-10-12 加，用户点名"流入流出有没有参照总市值折算"）= **当日换手强度**
    `板块成交额 ÷ 板块成分流通市值合计`，单位 %。share 剔"全场共同放量"，这一条剔"这个板块盘子本来就大"，
    两把尺子并列、不互相替换（同一场里 `银行` 份额名次 57 → 换手名次 822、`白银` 738 → 32）。
    分母是**当日日线反推**的市值（`100×成交额÷换手率`），不是 `stock_snapshot` —— 那张表只有 36 场。
    判据版本单独一号 `stn-1`，改换手口径不许把 share 那批行带走。
  * 攒不满窗口时 `days_N` 会诚实地小于 N，`cum_N` 是"实际那几场的差"——**两个 days 不同的板块不可比**，
    所以 `days_N` 必须和 `cum_N` 一起交出去。

用法：
    python -m app.compute.sector_flow_trend --date 2026-09-23
    python -m app.compute.sector_flow_trend --backfill 1300        # 全历史重算
    python -m app.compute.sector_flow_trend --basis main_net       # 未就绪会 skipped 并说明
    python -m app.compute.sector_flow_trend --dry-run              # 只算不写，无任何写库副作用
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import defaultdict
from typing import Any

from app.db import engine

SETTING_KEY = 'sector_flow_trend.params'
FALLBACK: dict[str, Any] = {
    'windows': [10, 20, 30],
    'sector_types': ['行业', '概念'],
    'basis': ['share'],
    'min_days_ratio': 0.6,          # 窗口内不足 60% 场次有数 → 该窗口的累计不给名次（给数但不给排名）
    'spec_version': 'sft-1',
    # turnover 是**另一把尺子**（换手强度），所以有自己一条判据版本；
    # 与 share 共用同一行参数，但版本号分开 —— 改换手口径不许把份额那批行也带走。
    'turnover_spec_version': 'stn-1',
    'turnover_chunk_days': 60,      # 一次取多少场的个股数据（见 run_turnover 的分块理由）
}
#: 实证随参数一起落进每一行，界面/事后的人都能看到"这不是信号"
EVIDENCE = ('份额连续 n 天抬升→次日仍抬升 46%(n=1，基线即 46%) 单调降到 31%(n=9)；'
            '样本 1249 场 × 963 板块，按交易日分层。见 docs/数据加工工作指南/08 坑 12')

#: turnover 那批行的实证：这条口径**为什么值得多一条**，用名次差说话（2026-10-12 现查）。
#: 这段要上界面的悬停，所以不写 markdown 星号。
TURNOVER_EVIDENCE = ('换手强度剔的是"这个板块盘子本来就大"，份额剔的是"全场共同放量"，两把尺子不是一回事：'
                     '2026-10-12 同一场里 银行 份额名次 57 → 换手名次 822、白银 738 → 32、光通信 824 → 146。'
                     '当日换手率分布：行业中位 1.79%（0.17~19.7）、概念中位 2.10%（0.38~11.1）。'
                     '本口径同样只交统计量：连续几场抬升不代表下一场继续 —— '
                     '份额那条实测 46%→31% 单调降（均值回复），换手没做过这个检验，别当信号用。')


def params() -> dict:
    """生效参数。库里那份赢 —— 且**只从这一行读**，不要用 `max(spec_version)` 字符串比。"""
    row = engine.query_one('select value from config.setting where key = %s', (SETTING_KEY,))
    raw = row.get('value') if row else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    out = dict(FALLBACK)
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k in FALLBACK})
    return out


def _f(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _int(x: Any) -> int | None:
    v = _f(x)
    return None if v is None else int(v)


def _streak(series: list[float]) -> tuple[int, int]:
    """以最后一项收尾的严格递增 / 严格递减连续场次数。**相等既不算升也不算降**（断链）。

    方向只看最后两场，再从尾部往回数同向的长度 —— 两个方向都数一遍再挑，
    会出现"序列一直在升、递减长度也数出 1"这种自相矛盾的返回值。
    """
    if len(series) < 2:
        return 0, 0
    last, prev = series[-1], series[-2]
    if last == prev:
        return 0, 0
    ascending = last > prev
    n = 1
    for i in range(len(series) - 1, 0, -1):
        same = series[i] > series[i - 1] if ascending else series[i] < series[i - 1]
        if not same:
            break
        n += 1
    return (n, 0) if ascending else (0, n)


def _fetch(p: dict, dmax: dt.date, need_sessions: int) -> tuple[dict, dict, list[dt.date]]:
    """取 `sector_daily` 的成交额，返回 (按板块的升序序列, 按场的分母, 场次表)。"""
    sql = """
        with days as (
            select distinct trade_date from market_data.sector_daily
             where trade_date <= %(dmax)s order by trade_date desc limit %(n)s
        )
        select d.sector_code, d.trade_date, d.amount_yuan, d.pct_change, i.sector_type
          from days join market_data.sector_daily d on d.trade_date = days.trade_date
          join reference_data.sector_info i on i.sector_code = d.sector_code
           and i.is_active and i.sector_type = any(%(types)s)
         order by d.sector_code, d.trade_date
    """
    rows = engine.query(sql, {'dmax': dmax, 'n': need_sessions,
                              'types': [str(t) for t in p['sector_types']]})
    series: dict[str, list[dict]] = defaultdict(list)
    totals: dict[dt.date, float] = defaultdict(float)
    for r in rows:
        amt = _f(r['amount_yuan'])
        series[r['sector_code']].append({'date': r['trade_date'], 'amount': amt,
                                         'pct': _f(r['pct_change'])})
        if amt:
            totals[r['trade_date']] += amt
    return series, dict(totals), sorted({r['trade_date'] for r in rows})


_UPSERT = """
insert into analytics.sector_flow_trend
  (sector_code, trade_date, basis, value, value_prev, day_delta_bps,
   streak_up_days, streak_dn_days, cum_10, cum_20, cum_30,
   days_10, days_20, days_30, rank_10, rank_20, rank_30,
   universe, universe_amount, amount_yuan, missing_days,
   params_snapshot, reasons, spec_version, calculated_at)
values
  (%(code)s, %(day)s, %(basis)s, %(value)s, %(value_prev)s, %(delta)s,
   %(up)s, %(dn)s, %(cum10)s, %(cum20)s, %(cum30)s,
   %(d10)s, %(d20)s, %(d30)s, %(r10)s, %(r20)s, %(r30)s,
   %(universe)s, %(uamt)s, %(amount)s, %(missing)s,
   %(params)s::jsonb, %(reasons)s::jsonb, %(spec)s, now())
on conflict (sector_code, trade_date, basis, spec_version) do update set
  value = excluded.value, value_prev = excluded.value_prev, day_delta_bps = excluded.day_delta_bps,
  streak_up_days = excluded.streak_up_days, streak_dn_days = excluded.streak_dn_days,
  cum_10 = excluded.cum_10, cum_20 = excluded.cum_20, cum_30 = excluded.cum_30,
  days_10 = excluded.days_10, days_20 = excluded.days_20, days_30 = excluded.days_30,
  rank_10 = excluded.rank_10, rank_20 = excluded.rank_20, rank_30 = excluded.rank_30,
  universe = excluded.universe, universe_amount = excluded.universe_amount,
  amount_yuan = excluded.amount_yuan, missing_days = excluded.missing_days,
  params_snapshot = excluded.params_snapshot, reasons = excluded.reasons,
  calculated_at = excluded.calculated_at
"""


def run_shares(target_dates: list[dt.date], p: dict, dry_run: bool) -> dict:
    """按板块一次遍历，再按天汇总名次。

    ⚠️ 这里刻意不做「每算一行都重扫一遍该板块的全部历史」：那是 行数 × 板块长度
    的乘积，30 天没感觉，1244 天（≈130 万行）会跑到天荒地老 ——
    `sector_trend_signal` 今天就是这么栽过一次，索引结构一次建好才降到秒级。
    """
    ws = [int(w) for w in p['windows']]
    w_max = max(ws)
    spec = str(p['spec_version'])
    need = w_max + 2 + len(target_dates)
    series, totals, days = _fetch(p, max(target_dates), need)
    if not days:
        return {'rows': 0, 'skipped': '目标区间内 `sector_daily` 没有成交额行（上游 daily/c3 还没入库）'}

    # 每板块一条"只含有份额的"升序序列（份额为空的场次直接不入列，用 days_N 交出少了哪几场）
    sv_dates: dict[str, list[dt.date]] = {}
    sv_vals: dict[str, list[float]] = {}
    for code, rows in series.items():
        dts, vals = [], []
        for r in rows:
            tot = totals.get(r['date'])
            if r['amount'] and tot:
                dts.append(r['date'])
                vals.append(r['amount'] / tot)
        if len(vals) >= 2:
            sv_dates[code] = dts
            sv_vals[code] = vals

    # 一次建好的「日 → (板块, 该板块序列里的下标)」索引：按天找板块要 O(板块数) 而不是 O(板块数 × 序列长)
    day_index: dict[dt.date, list[tuple[str, int]]] = defaultdict(list)
    for code, dts in sv_dates.items():
        for pos, d in enumerate(dts):
            day_index[d].append((code, pos))

    targets = set(target_dates)
    rows_out: list[dict] = []
    written = 0
    per_day: dict[str, dict] = {}
    no_data: list[str] = []
    for day in sorted(targets):
        present = day_index.get(day, [])
        universe = len(present)
        if not universe:
            no_data.append(str(day))
            per_day[str(day)] = {'sectors': 0}
            continue
        rows_day, cums = [], {w: {} for w in ws}
        for code, pos in present:
            if pos < 1:
                continue
            dts, vals = sv_dates[code], sv_vals[code]
            up, dn = _streak(vals[:pos + 1])
            cum, days_n = {}, {}
            for w in ws:
                lo = max(0, pos - w + 1)
                cum[w] = round(vals[pos] - vals[lo], 12)
                days_n[w] = pos - lo + 1
            for w in ws:
                if days_n[w] >= max(2, int(round(w * float(p['min_days_ratio'])))):
                    cums[w][code] = cum[w]
            amount = next((r['amount'] for r in series[code] if r['date'] == day), None)
            rows_day.append({
                'code': code, 'day': day, 'basis': 'share',
                'value': round(vals[pos], 12), 'value_prev': round(vals[pos - 1], 12),
                'delta': round((vals[pos] - vals[pos - 1]) * 1e4, 3),
                'up': up, 'dn': dn,
                'cum10': cum.get(10), 'cum20': cum.get(20), 'cum30': cum.get(30),
                'd10': days_n.get(10), 'd20': days_n.get(20), 'd30': days_n.get(30),
                'universe': universe, 'uamt': totals.get(day), 'amount': amount,
                'missing': w_max - min(days_n.values()),
                'params': json.dumps({**p, 'evidence': EVIDENCE}, ensure_ascii=False, default=str),
                'reasons': json.dumps({
                    'windows': ws, 'streak_basis': '份额严格递增场次（相等断链）',
                    'days_with_value': days_n,
                    'window_first_date': str(dts[max(0, pos - w_max + 1)]),
                    'universe_amount': totals.get(day), 'evidence': EVIDENCE},
                    ensure_ascii=False, default=str),
                'spec': spec})
        ranks = {w: {c: i for i, (c, _) in enumerate(
                        sorted(vals_.items(), key=lambda kv: -kv[1]), start=1)}
                 for w, vals_ in cums.items()}
        for r in rows_day:
            r['r10'] = ranks[10].get(r['code'])
            r['r20'] = ranks[20].get(r['code'])
            r['r30'] = ranks[30].get(r['code'])
        rows_out.extend(rows_day)
        per_day[str(day)] = {'sectors': len(rows_day)}
        # 一天一写，不在内存里攒全批：全历史 ≈120 万行，攒到最后一次写
        # 既是内存峰值也是"中途被打断就一行没有"的那种形状
        if rows_out and not dry_run and len(rows_out) >= 2000:
            written += _flush(rows_out)
            rows_out = []

    if rows_out and not dry_run:
        written += _flush(rows_out)
    return {'rows': written, 'basis': 'share', 'per_day': per_day,
            'spec_version': spec, 'windows': ws, 'no_data_days': no_data, 'dry_run': dry_run}


def _flush(batch: list[dict]) -> int:
    engine.executemany(_UPSERT, batch)
    return len(batch)


#: turnover 口径专用的 upsert。刻意不复用 `_UPSERT`：那条是 share 路径（有测试钉住）在走的语句，
#: 把四个换手专用列塞进去会让 share 那批行也带一堆 None 参数，两边判据一改就互相牵连。
_UPSERT_T = """
insert into analytics.sector_flow_trend
  (sector_code, trade_date, basis, value, value_prev, day_delta_bps,
   streak_up_days, streak_dn_days, cum_10, cum_20, cum_30,
   days_10, days_20, days_30, rank_10, rank_20, rank_30,
   universe, universe_amount, amount_yuan, missing_days,
   mv_yuan, amount_matched, members_matched, members_total,
   params_snapshot, reasons, spec_version, calculated_at)
values
  (%(code)s, %(day)s, 'turnover', %(value)s, %(value_prev)s, %(delta)s,
   %(up)s, %(dn)s, %(cum10)s, %(cum20)s, %(cum30)s,
   %(d10)s, %(d20)s, %(d30)s, %(r10)s, %(r20)s, %(r30)s,
   %(universe)s, null, %(amount)s, %(missing)s,
   %(mv)s, %(amt_matched)s, %(matched)s, %(members)s,
   %(params)s::jsonb, %(reasons)s::jsonb, %(spec)s, now())
on conflict (sector_code, trade_date, basis, spec_version) do update set
  value = excluded.value, value_prev = excluded.value_prev, day_delta_bps = excluded.day_delta_bps,
  streak_up_days = excluded.streak_up_days, streak_dn_days = excluded.streak_dn_days,
  cum_10 = excluded.cum_10, cum_20 = excluded.cum_20, cum_30 = excluded.cum_30,
  days_10 = excluded.days_10, days_20 = excluded.days_20, days_30 = excluded.days_30,
  rank_10 = excluded.rank_10, rank_20 = excluded.rank_20, rank_30 = excluded.rank_30,
  universe = excluded.universe, universe_amount = excluded.universe_amount,
  amount_yuan = excluded.amount_yuan, missing_days = excluded.missing_days,
  mv_yuan = excluded.mv_yuan, amount_matched = excluded.amount_matched,
  members_matched = excluded.members_matched, members_total = excluded.members_total,
  params_snapshot = excluded.params_snapshot, reasons = excluded.reasons,
  calculated_at = excluded.calculated_at
"""

#: 一场的个股侧数据 → 每个板块的「成员成交额合计 / 成分流通市值合计 / 匹配成员数」。
#: 分块取（见 run_turnover 的理由），`%(n)s` = 这一批目标日 + 窗口长度。
_TURNOVER_FETCH = """
with days as (
    select distinct trade_date from market_data.stock_daily
     where trade_date <= %(dmax)s order by trade_date desc limit %(n)s
),
st as (
    select left(sd.security_code, 6) as code, sd.trade_date as d,
           sd.amount_yuan as a, sd.turnover_rate_percent as t
      from market_data.stock_daily sd join days using (trade_date)
     where nullif(sd.amount_yuan, 0) is not null
       and nullif(sd.turnover_rate_percent, 0) is not null
),
mem as (
    select i.sector_code, left(mm.security_code, 6) as code, mm.in_date, mm.out_date
      from reference_data.sector_member mm
      join reference_data.sector_info i using (sector_code)
     where i.is_active and i.sector_type = any(%(types)s)
),
pair as (
    -- 成员按那一天对齐（as-of）。拿"今天的成员表"回看历史 = 给历史塞未来信息。
    select m.sector_code, p.d, p.a, 100.0 * p.a / p.t as mv
      from mem m join st p on p.code = m.code
     where (m.in_date is null or m.in_date <= p.d)
       and (m.out_date is null or m.out_date > p.d)
),
agg as (
    select sector_code, d, sum(a) as amt_matched, sum(mv) as mv, count(*) as matched
      from pair group by 1, 2
)
select agg.sector_code, agg.d as trade_date,
       agg.amt_matched, agg.mv, agg.matched::int as matched,
       sd.amount_yuan as sector_amount, sd.member_count::int as member_count
  -- 内连接 = 母体门：**只算当天在 sector_daily 有行且有成交额的板块**。
  -- 字典里 行业+概念 共 1,859 个，但其中约一半（`THS_*`、`GN_*` 这些别的源登记的）
  -- 通达信板块日线里当天没有行；按成员聚合会把它们也算进来（实测 1,856 vs share 的 915），
  -- 于是两条口径的名次分母不同、并排一比就是假话。share 与 turnover 必须同一个母体。
  from agg join market_data.sector_daily sd
    on sd.sector_code = agg.sector_code and sd.trade_date = agg.d
 where sd.amount_yuan > 0
 order by agg.sector_code, agg.d
"""


def run_turnover(target_dates: list[dt.date], p: dict, dry_run: bool) -> dict:
    """换手强度：`板块成交额 ÷ 板块成分流通市值合计`，单位 %（当日换手率）。

    与 share 那条的分工：share 剔"全场共同放量"，**不剔"这个板块盘子本来就大"**；
    这条剔后者。同一场里两者名次差得很远（2026-10-12 实测：`银行` 份额名次 57 → 换手名次 822，
    `白银` 738 → 32），所以它是新信息，不是 share 的另一种写法。

    三处刻意：
      ① **分母不用 `stock_snapshot`**：那张表实测只有 36 场（2026-07-22 起、还稀疏），
         近 10/20/30 场的窗口根本攒不满。改成当日日线反推
         `流通市值 = 100 × 成交额 ÷ 换手率`（`turnover_rate_percent` 全史 2021→2026 覆盖 100.0%）。
         与快照同日逐票对照过：中位绝对差 0.911%、p90 2.74%（同源，差在四舍五入与抓取时点）。
      ② **分子只用"能反推出市值的那些成员"的和**，不用 `sector_daily.amount_yuan`：
         板块成交额里含着当天停牌（无换手率）的那几只的钱，市值却进不了分母 —— 直接用就虚高。
         两边严格同一母体，并交 `members_matched`/`members_total` 让读者看见剔了多少。
      ③ **按场分块取数**（`turnover_chunk_days`，默认 60 场）：个股×成员的 join 一场约 5 千×20 成员，
         一把跑 1,254 场会把内存与临时空间吃掉一大截；分块后每块都是"块长 + 窗口长"的窗口，
         跨块的窗口边界靠多取 `w_max` 场历史覆盖，不靠"上一块的结果"传递 —— 块间断了也能重跑。
    """
    ws = [int(w) for w in p['windows']]
    w_max = max(ws)
    spec = str(p.get('turnover_spec_version') or 'stn-1')
    chunk = max(1, int(p.get('turnover_chunk_days') or 60))
    types = [str(t) for t in p['sector_types']]
    targets = sorted(set(target_dates))
    written = 0
    per_day: dict[str, dict] = {}
    no_data: list[str] = []

    for lo in range(0, len(targets), chunk):
        block = targets[lo:lo + chunk]
        need = len(block) + w_max + 2
        rows = engine.query(_TURNOVER_FETCH,
                            {'dmax': block[-1], 'n': need, 'types': types}) or []
        # 板块 → 升序 (日期, 换手率%)；换手率 = 100 × 成员成交额合计 ÷ 成员市值合计
        sv_dates: dict[str, list[dt.date]] = defaultdict(list)
        sv_vals: dict[str, list[float]] = defaultdict(list)
        sv_extra: dict[str, dict[dt.date, dict]] = defaultdict(dict)
        for r in rows:
            amt, mv = _f(r['amt_matched']), _f(r['mv'])
            if not amt or not mv or amt <= 0 or mv <= 0:
                continue
            code = str(r['sector_code'])
            day = r['trade_date']
            sv_dates[code].append(day)
            sv_vals[code].append(round(100.0 * amt / mv, 12))
            sv_extra[code][day] = {'mv': mv, 'amt': amt, 'matched': _int(r['matched']),
                                   'members': _int(r['member_count']) or _int(r['matched']),
                                   'sector_amount': _f(r['sector_amount'])}
        if not sv_dates:
            for d in block:
                no_data.append(str(d))
            continue

        day_index: dict[dt.date, list[tuple[str, int]]] = defaultdict(list)
        for code, dts in sv_dates.items():
            for pos, d in enumerate(dts):
                day_index[d].append((code, pos))

        for day in block:
            present = day_index.get(day, [])
            if not present:
                no_data.append(str(day))
                per_day[str(day)] = {'sectors': 0}
                continue
            universe = len(present)
            rows_day, cums = [], {w: {} for w in ws}
            for code, pos in present:
                if pos < 1:
                    continue
                dts, vals = sv_dates[code], sv_vals[code]
                ex = sv_extra[code][day]
                up, dn = _streak(vals[:pos + 1])
                cum, days_n = {}, {}
                for w in ws:
                    start = max(0, pos - w + 1)
                    cum[w] = round(vals[pos] - vals[start], 12)
                    days_n[w] = pos - start + 1
                for w in ws:
                    if days_n[w] >= max(2, int(round(w * float(p['min_days_ratio'])))):
                        cums[w][code] = cum[w]
                rows_day.append({
                    'code': code, 'day': day,
                    'value': round(vals[pos], 8), 'value_prev': round(vals[pos - 1], 8),
                    # turnover 的 value 已经是百分数 ⇒ 差值 ×100 才是 bp（与 share 口径的 ×1e4 同义）
                    'delta': round((vals[pos] - vals[pos - 1]) * 100.0, 3),
                    'up': up, 'dn': dn,
                    'cum10': cum.get(10), 'cum20': cum.get(20), 'cum30': cum.get(30),
                    'd10': days_n.get(10), 'd20': days_n.get(20), 'd30': days_n.get(30),
                    'universe': universe,
                    'amount': ex['sector_amount'],          # 对照列：板块日线给的板块成交额
                    'mv': round(ex['mv'], 4), 'amt_matched': round(ex['amt'], 4),
                    'matched': ex['matched'], 'members': ex['members'],
                    'missing': w_max - min(days_n.values()),
                    'params': json.dumps({**p, 'evidence': TURNOVER_EVIDENCE},
                                         ensure_ascii=False, default=str),
                    'reasons': json.dumps({
                        'windows': ws, 'unit': '当日换手率 %（不是金额、不是份额）',
                        'mv_source': '100×成交额÷turnover_rate_percent 反推（stock_snapshot 只有 36 场，撑不起窗口）',
                        'membership': 'sector_member 按 in_date/out_date 对齐到那一天',
                        'days_with_value': days_n,
                        'window_first_date': str(dts[max(0, pos - w_max + 1)]),
                        'evidence': TURNOVER_EVIDENCE}, ensure_ascii=False, default=str),
                    'spec': spec})
            ranks = {w: {c: i for i, (c, _) in enumerate(
                            sorted(v.items(), key=lambda kv: -kv[1]), start=1)}
                     for w, v in cums.items()}
            for r in rows_day:
                r['r10'] = ranks[10].get(r['code'])
                r['r20'] = ranks[20].get(r['code'])
                r['r30'] = ranks[30].get(r['code'])
            per_day[str(day)] = {'sectors': len(rows_day)}
            if rows_day and not dry_run:
                engine.executemany(_UPSERT_T, rows_day)
                written += len(rows_day)
            elif rows_day:
                written += len(rows_day)
    return {'rows': written, 'basis': 'turnover', 'per_day': per_day, 'spec_version': spec,
            'windows': ws, 'chunk_days': chunk, 'no_data_days': no_data, 'dry_run': dry_run}


def run_main_net(target_dates: list[dt.date], p: dict) -> dict:
    """主力净额那条路：逐日数据没入库就**明说不做**，不用代理值凑一个看着像的东西。"""
    r = engine.query_one("""select count(*) rows, count(distinct trade_date) days,
                                   max(trade_date) hi from market_data.fund_flow_rank""", role='reader') or {}
    need = max(int(w) for w in p['windows'])
    rows = int(r.get('rows') or 0)
    days = int(r.get('days') or 0)
    if days < need:
        return {'basis': 'main_net', 'rows': 0, 'sessions_available': days,
                'sessions_needed': need, 'latest': str(r.get('hi') or ''),
                'skipped': ('`market_data.fund_flow_rank` 只有 %s 行 / %s 场（要算近 %s 场累计至少要 %s 场）。'
                            '这条通道的采集还没进调度：链上跑的 `c11_fundflow` 走已被掐的东财 push2，'
                            '新浪那条 `c21_fundflow_sina` 未挂 —— 归《数据获取》接上后每个交易日攒一场。'
                            '⚠️ 逐日序列事后补不出来：今天不攒，10 天后还是没有 10 天。'
                            % (f'{rows:,}', days, need, need))}
    return {'basis': 'main_net', 'rows': 0,
            'skipped': '表里已有 %s 场，但 main_net 这条计算还没接（先确认份额那条读数被用得住，再上第二条口径）'
                       % days}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='资金轮动趋势（统计量，非信号）')
    ap.add_argument('--date', action='append', default=[], help='目标交易日，可重复')
    ap.add_argument('--backfill', type=int, default=0, help='回算最近 N 个交易日')
    ap.add_argument('--basis', action='append', default=[],
                    choices=['share', 'main_net', 'turnover'])
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    p = params()
    bases = a.basis or [str(b) for b in p['basis']]

    days = [dt.date.fromisoformat(x) for x in a.date]
    if not days:
        last = engine.scalar('select max(trade_date) from market_data.sector_daily')
        days = [last] if last else []
    if a.backfill > 1:
        hist = engine.query("""select distinct trade_date from market_data.sector_daily
                                order by trade_date desc limit %s""", (a.backfill,))
        days = sorted(r['trade_date'] for r in hist)
    if not days:
        print('没有目标交易日（sector_daily 是空的？）')
        return 1

    results = []
    if 'share' in bases:
        results.append(run_shares(days, p, a.dry_run))
    if 'turnover' in bases:
        results.append(run_turnover(days, p, a.dry_run))
    if 'main_net' in bases:
        results.append(run_main_net(days, p))
    for r in results:
        print(json.dumps(r, ensure_ascii=False, default=str, indent=2)[:1200])

    # 每一条跑过的口径都要单独判"今天到底有没有数"。原先只有 share 一条门：
    # 加第二条口径时如果忘了配门，换手那条天天空跑而退出码还是 0，界面上就是一张永远为空的榜。
    share = next((r for r in results if r.get('basis') == 'share'), {})
    if share.get('skipped') or (share.get('no_data_days') and not share.get('rows')
                                and not a.dry_run):
        print('**目标交易日在 sector_daily 里没有成交额行**：%s —— 上游 daily/c3 还没入库，本步不算成功'
              % '、'.join(share.get('no_data_days') or []))
        return 1
    turn = next((r for r in results if r.get('basis') == 'turnover'), {})
    if turn.get('no_data_days') and not turn.get('rows') and not a.dry_run:
        print('**换手强度这一条一条都没算出来**：%s —— 个股日线缺成交额或缺换手率字段'
              '（`turnover_rate_percent` 是反推流通市值的唯一分母来源，缺它就凑不出分母）；'
              '本步不算成功，不去拿只有 36 场的 stock_snapshot 凑数'
              % '、'.join(turn['no_data_days'][:5]))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
