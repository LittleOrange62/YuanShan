# -*- coding: utf-8 -*-
"""分配者视角 —— 把《A股找分配者》那段话变成四个可复算的读数。

判据与来源：`docs/分配者视角工作指南.md`（原始视频原文也在那份里，逐段对得上）。
一句话：**不判"谁创造了财富"，只算"钱必须经过谁"和"谁正站在被灌满的河道上"。**

四块读数，每块都只用了本库真有的东西：

| 块 | 列 | 用的数据 | 本地代理与它的边界 |
|---|---|---|---|
| ① 位置（收费站分） | `toll_score` | 板块内 A 股 TTM 股息率中位（`reference_data.dividend` ÷ `market_data.stock_snapshot` 市值）+ 日成交额稳定性 + 份额长期中位 + 板块市值占比 | **"绕不过去"这件事本库没有数据**（没有财务、没有牌照/市占率），所以这里全部是**形状代理**：长期稳定地有现金流经过 = 像收费站。形状像不等于制度上是 |
| ② 拥挤度温度 | `crowding_temp` | 当日份额在自己近 250 场里的分位 + 份额涌入速度 + 板块成员数一年增量 + 融资余额/流通市值 | 温度 100 = "所有人终于达成共识"，**不是卖出信号**；成员数是"很多人同时上项目"的唯一本地证据，不是产能数据 |
| ③ 再分配生命周期 | `stage` + `stage_reason` | 份额水平 × 份额方向 × 温度 × 价格动量的判定树 | 只有资金与价格的形状，**不含政策文本**（本地没有政策库）；所以它说的是"这波钱走到哪一段"，不是"政策还会不会加" |
| ④ 位置 vs 想象力 | `imagine_score` + `quadrant` | 涨幅、广度、份额热度、概念属性 对 位置分 | 象限分界线取**当日中位数**，不写死 50 |

三条不许破的纪律（这套分数最容易骗人的地方）：

1. **算不出是 NULL，不是 0。** NULL 不参与排名也不给分；同时把覆盖度
   （`div_coverage` / `mv_coverage`）一起交出去 —— 不然"板块股息率中位"会被读成
   "整个板块的股息率"，而它可能只算了三分之一的成员。
2. **权重是**可商量的先验**，不是市场规律**（35/25/20/10 这类数字都是拍出来的起点），
   所以每一项的原始读数都单独进表：改权重是新算一批行，历史那批不动。
3. **`spec_version` 进主键**。判据一改就是新版本、新行；旧行永远保持当时那句话。

用法：

    python -m app.compute.allocator                      # 最近一个已收盘交易日
    python -m app.compute.allocator --date 2026-09-30
    python -m app.compute.allocator --since 2026-09-01    # 补一段
    python -m app.compute.allocator --limit-sectors 60     # 只算头部，试形状用
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from typing import Any, Iterable

from app.db import engine

SPEC_VERSION = 'alloc-1'

#: 母体：只有行业 + 概念进分母。行为统计类板块（"昨日涨停"这一类）不进 ——
#: 它们的名字就是当天涨跌的结果，拿它们算"钱往哪儿走"等于用结果解释原因。
SECTOR_TYPES = ('行业', '概念')

#: 窗口。250 场约一年：份额分位、稳定性、成员增量都按这一年算。
WINDOW = 250
SLOPE_SKIP = 5           # 涌入速度的基线跳过最近 5 场（否则基线被自己这一波抬高）
MOM_WINDOW = 60
BREADTH_WINDOW = 20
MEMBER_LAG = 240         # 与约一年前比成员数

#: 权重（**可商量的先验**，不是规律）。缺项时按可用项重新归一化，并记进 `missing`。
TOLL_W = {'div_yield': 35, 'stability': 25, 'share_med': 20, 'mv_share': 20}
CROWD_W = {'share_pctile': 45, 'inflow': 25, 'member_delta': 20, 'margin': 10}
IMAGINE_W = {'mom': 40, 'breadth': 25, 'heat': 20, 'concept': 15}

#: 阶段判定的阈值。每个数都在 `stage_reason` 里原样带出去，事后必须能复算出同一句话。
STAGE_P = {'consensus_temp': 80.0, 'consensus_pctile': 0.90,
           'inflow_slope': 0.30, 'inflow_pctile': 0.60,
           'boom_pctile_lo': 0.40, 'boom_pctile_hi': 0.90,
           'seed_pctile': 0.40, 'overhang_pctile': 0.35}


# ---------------------------------------------------------------- 纯判据（可单测，不碰库）

def _pct_rank(values: list[float], x: float) -> float | None:
    """`x` 在 `values` 里的分位（0~1）。样本不足 5 个就交回 None —— 3 个点算出的分位是噪音。"""
    xs = [v for v in values if v is not None and not math.isnan(v)]
    if len(xs) < 5 or x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    below = sum(1 for v in xs if v <= x)
    return round(below / len(xs), 4)


def score_from_parts(parts: dict[str, float | None], weights: dict[str, int]) -> tuple[float | None, list[str]]:
    """按**可用项**归一化加权，缺的项记进 `missing`。

    为什么不把缺项当 0：`analytics.score` 那套的教训 —— 0 分会参与排名，
    于是"没数据"被排成"很差"，两者是完全不同的处境。这里缺项直接不进分母，
    同时把"缺了哪几项"交出去，界面上必须能看见这一行是按几分算的。
    """
    have = {k: v for k, v in parts.items() if v is not None and not (isinstance(v, float) and math.isnan(v))}
    missing = sorted(set(parts) - set(have))
    if not have:
        return None, missing
    tw = sum(weights[k] for k in have)
    if tw <= 0:
        return None, missing
    val = sum(have[k] * weights[k] for k in have) / tw * 100.0
    return round(val, 2), missing


def judge_stage(*, share_pctile: float | None, share_slope20: float | None,
                crowding_temp: float | None, mom60: float | None,
                member_delta: int | None) -> tuple[str | None, str]:
    """再分配生命周期五段，**互斥、按顺序判**，都不满足交回 `None`（不硬塞一个）。

    顺序是有讲究的：先判最贵的两个处境（共识/过剩），再判中间三段，
    因为"份额高 + 价格还在涨"与"份额高 + 价格已回落"是两种完全相反的行动，
    把它们排在同一档就会把顶部读成起点。
    """
    p = STAGE_P
    if share_pctile is None or mom60 is None:
        return None, '份额分位或动量算不出，不猜阶段'
    if (crowding_temp or 0) >= p['consensus_temp'] and share_pctile >= p['consensus_pctile'] and mom60 > 0:
        return 's4_consensus', ('份额已到自身近 %d 场的 %s 分位、温度 %s ≥%s、60 场动量 %+0.1f%% —— '
                                '所有人终于达成共识（视频原话：最危险的不是没人信，是所有人都信了）'
                                % (WINDOW, _pc(share_pctile), _n(crowding_temp),
                                   _n(p['consensus_temp']), mom60))
    if share_pctile < p['overhang_pctile'] and mom60 < 0 and (member_delta or 0) > 0:
        return 's5_overhang', ('份额分位只剩 %s、60 场动量 %+0.1f%% 向下，而板块成员数一年多了 %s 个 —— '
                               '价格和份额先掉头、产能还在进场（"股票先于产业掉头"的形状）'
                               % (_pc(share_pctile), mom60, member_delta))
    if (share_slope20 is not None and share_slope20 > p['inflow_slope']
            and share_pctile >= p['inflow_pctile']):
        return 's3_inflow', ('份额比前 %d 场（跳过最近 %d 场）的均值高 %s，自身分位 %s —— 钱正在加速灌进来'
                             % (WINDOW // 12, SLOPE_SKIP, _signed_pct(share_slope20), _pc(share_pctile)))
    if p['boom_pctile_lo'] <= share_pctile < p['boom_pctile_hi'] and mom60 > 0:
        return 's2_boom', ('份额分位 %s 在中段、60 场动量 %+0.1f%% 向上 —— 产业繁荣但还没挤满'
                           % (_pc(share_pctile), mom60))
    if share_pctile < p['seed_pctile'] and mom60 > 0 and (share_slope20 or 0) > 0:
        return 's1_seed', ('份额分位只有 %s（还没被挤满）、但份额开始抬头、动量 %+0.1f%% —— '
                           '权力刚转身、市场还没拥挤的那一段' % (_pc(share_pctile), mom60))
    return None, ('条件都不满足（份额分位 %s、动量 %+0.1f%%、温度 %s）—— 不在五段里就是不在，'
                  '不硬贴一个阶段' % (_pc(share_pctile), mom60, _n(crowding_temp)))


def quadrant_of(*, toll: float | None, imagine: float | None,
                toll_median: float | None, imagine_median: float | None) -> str | None:
    """位置 vs 想象力。分界线是**当日中位数**：写死 50 的话，分布一动整个象限全体搬家。"""
    if None in (toll, imagine, toll_median, imagine_median):
        return None
    hi_toll, hi_imag = toll >= toll_median, imagine >= imagine_median
    if hi_toll and hi_imag:
        return 'consensus'      # 又有位置又有想象力 = 所有人都信了
    if hi_toll:
        return 'toll'           # 有位置、没想象力 = 嫌它无聊的收费站
    if hi_imag:
        return 'story'          # 只有想象力 = 故事区
    return 'nowhere'


QUADRANT_LABEL = {
    'toll': '收费站（位置高·想象力低）',
    'consensus': '共识区（位置高·想象力也高）',
    'story': '故事区（位置低·想象力高）',
    'nowhere': '无人区（两头都不占）',
}
STAGE_LABEL = {'s1_seed': 'S1 起步·权力刚转身', 's2_boom': 'S2 繁荣',
               's3_inflow': 'S3 资本涌入', 's4_consensus': 'S4 共识·估值疯狂',
               's5_overhang': 'S5 过剩·股价先掉头'}


def _pc(x: float | None) -> str:
    return '—' if x is None else '%.0f%%' % (x * 100)


def _n(x: Any) -> str:
    return '—' if x is None else ('%g' % x)


def _signed_pct(x: float | None) -> str:
    return '—' if x is None else '%+.0f%%' % (x * 100)


# ---------------------------------------------------------------- 取数

def _sessions(target: dt.date, n: int) -> list[dt.date]:
    rows = engine.query(
        """select distinct d.trade_date from market_data.sector_daily d
         join reference_data.sector_info i using (sector_code)
         where d.trade_date <= %s and """ + SECTOR_OK + """
         order by 1 desc limit %s""", (target, n))
    return sorted(r['trade_date'] for r in rows)


def _sector_daily(lo: dt.date, hi: dt.date, sector: str | None) -> list[dict]:
    """板块日线 + 当日全场板块成交额（份额的分母）。**份额用板块成交额之和，不含个股与指数。**"""
    lim = ''
    params: list[Any] = [lo, hi]
    if sector:
        lim = ' and d.sector_code = %s'
        params.append(sector)
    return engine.query(
        """with d as (
             select d.sector_code, d.trade_date, d.amount_yuan, d.close, d.pct_change,
                    d.up_count, d.down_count, d.member_count, i.name as sector_name, i.sector_type
             from market_data.sector_daily d
             join reference_data.sector_info i using (sector_code)
             where """ + SECTOR_OK + """ and d.trade_date between %s and %s""" + lim + """
           ), tot as (select trade_date, sum(nullif(amount_yuan,0)) as all_amount from d group by 1)
           select d.*, case when t.all_amount > 0 then d.amount_yuan / t.all_amount end as share
           from d join tot t using (trade_date) order by d.sector_code, d.trade_date""",
        tuple(params[:2]) + (tuple(params[2:]) if sector else ()))


#: 母体口径只写这一处：**行业 + 概念，且必须有真名字**。
#:
#: 排除的两类各有理由：① 行为统计类板块（`TDX_SP_*`：融资融券、昨日涨停这一类）
#: 在 `sector_type='自定义'` 里，类型过滤就挡住了 —— 它们的名字就是当天的结果，
#: 拿它们算"钱往哪儿走"等于用结果解释原因；② 880001~880099 那 48 个
#: 现在是 `sector_type='统计'`（2026-10-06 更正：它们是通达信的**市场级统计指数**
#: —— 总市值 / 涨跌家数 / 主板总值…，成交额逐日等于全 A，不是"某个行业"，
#: 也不是早前以为的"缺名字的板块"），类型过滤同样挡住。
#: 名字那一半条件仍然留着，它挡的是**字典里连名字都没有**的行（如 880595）：
#: 2026-10-06 首跑实测就是被这批顶上去的 —— 收费站榜首全是 880099/880017，
#: 而且它们的市值占比一模一样（0.3577）= 同一个成员集被重复计入。
SECTOR_OK = ("i.sector_type in ('行业','概念') "
             "and i.name is not null and i.name <> replace(i.sector_code, 'TDX_', '')")


def _dividend_yields(target: dt.date) -> dict[str, float]:
    """每股 TTM 现金股息率 = 近 365 天除息的现金分红 ÷ 最近一场收盘价。

    分母为什么用 `stock_daily.close` 而不是快照里反推的价格：**实测 `stock_snapshot`
    的 `bps` 与 `eps` 全表为 0**（2026-10-06，5,578 行一个非零都没有），
    `pb × bps` 反推出来的价格是 NULL，于是整块股息率算不出、收费站分全体退化。
    用不复权收盘价配每股现金分红是对的口径 —— 两边都是"原始股数"上的钱。

    边界（必须写进界面说明）：分子是 TTM 现金分红，分母是**当前**价，
    所以这一项是"当下的读数"，不能回算历史；快照/日线没覆盖的个股算不出（交回缺，不给 0）。
    送转与配股不进分子（那是股本变化，不是现金分红）。
    """
    rows = engine.query(
        """with dv as (
             select security_code, sum(cash_per_share) as cash_12m
             from reference_data.dividend
             where ex_date > %s - interval '365 day' and ex_date <= %s and cash_per_share > 0
             group by 1
           ), px as (
             select security_code, close from market_data.stock_daily
             where trade_date = (select max(trade_date) from market_data.stock_daily where trade_date <= %s)
               and close > 0
           )
           select dv.security_code, dv.cash_12m, px.close
           from dv join px using (security_code)""", (target, target, target))
    out: dict[str, float] = {}
    for r in rows:
        try:
            out[r['security_code']] = float(r['cash_12m']) / float(r['close'])
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    return out


def _members_at(day: dt.date) -> dict[str, list[str]]:
    """板块成员按**时点**取（`sector_member` 有 `in_date`/`out_date`，这是自选组做不到的）。

    只用在场成分算股息率与市值 —— 拿"今天知道的名单"回算过去是未来函数。
    """
    rows = engine.query(
        """select m.sector_code, m.security_code
           from reference_data.sector_member m
           join reference_data.sector_info i using (sector_code)
           join reference_data.security s using (security_code)
           where """ + SECTOR_OK + """ and s.asset_type = 'stock'
             and (m.in_date is null or m.in_date <= %s)
             and (m.out_date is null or m.out_date > %s)""", (day, day))
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r['sector_code'], []).append(r['security_code'])
    return out


def _member_counts(day: dt.date) -> dict[str, int]:
    rows = engine.query(
        """select m.sector_code, count(*) n
           from reference_data.sector_member m
           join reference_data.sector_info i using (sector_code)
           join reference_data.security s using (security_code)
           where """ + SECTOR_OK + """ and s.asset_type = 'stock'
             and (m.in_date is null or m.in_date <= %s)
             and (m.out_date is null or m.out_date > %s)
           group by 1""", (day, day))
    return {r['sector_code']: int(r['n']) for r in rows}


def _market_values(target: dt.date) -> tuple[dict[str, float], float]:
    rows = engine.query(
        """select security_code, total_market_value_yuan from market_data.stock_snapshot
           where trade_date = (select max(trade_date) from market_data.stock_snapshot where trade_date <= %s)
             and total_market_value_yuan > 0""", (target,))
    mv = {r['security_code']: float(r['total_market_value_yuan']) for r in rows}
    return mv, sum(mv.values())


def _margin_ratio(target: dt.date) -> dict[str, float]:
    """融资余额 ÷ 流通市值（个股级）。本地两融只有 2026-06 起，缺的日子就是缺。

    列名是实测的：这张表叫 `financing_balance`，不叫 `margin_balance_yuan`。
    """
    rows = engine.query(
        """with m as (select security_code, sum(financing_balance) as bal
                      from market_data.margin_detail
                      where trade_date = (select max(trade_date) from market_data.margin_detail where trade_date <= %s)
                      group by 1),
             s as (select security_code, float_market_value_yuan from market_data.stock_snapshot
                   where trade_date = (select max(trade_date) from market_data.stock_snapshot where trade_date <= %s))
           select m.security_code, m.bal, s.float_market_value_yuan
           from m join s using (security_code) where s.float_market_value_yuan > 0""",
        (target, target))
    out: dict[str, float] = {}
    for r in rows:
        try:
            if r['bal'] and float(r['float_market_value_yuan']) > 0:
                out[r['security_code']] = float(r['bal']) / float(r['float_market_value_yuan'])
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------- 逐板块汇总

def _series(rows: Iterable[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r['sector_code'], []).append(r)
    return out


def _roll_up(day_rows: list[dict], *, member_delta: int | None, div_yield: float | None,
             div_cov: float | None, mv_share: float | None, mv_cov: float | None,
             margin: float | None) -> dict:
    """把一个板块近 250 场的行压成一行读数。**点数不够就是算不出**，不缩短窗口硬算。"""
    shares = [float(r['share']) for r in day_rows if r['share'] is not None]
    amounts = [float(r['amount_yuan']) for r in day_rows if r['amount_yuan'] is not None]
    closes = [float(r['close']) for r in day_rows if r['close'] is not None]
    breadths = [float(r['up_count']) / float(r['member_count'])
                for r in day_rows
                if r['up_count'] is not None and r['member_count'] not in (None, 0)]
    share = shares[-1] if shares else None
    share_pctile = _pct_rank(shares, share) if shares else None
    share_med = _median(shares)
    stability = None
    if len(amounts) >= 30:
        mean = sum(amounts) / len(amounts)
        if mean > 0:
            var = sum((a - mean) ** 2 for a in amounts) / len(amounts)
            stability = round(max(0.0, min(1.0, 1.0 - math.sqrt(var) / mean)), 3)
    slope = None
    if len(shares) > SLOPE_SKIP + 10:
        base = shares[-(SLOPE_SKIP + 21):-SLOPE_SKIP] if len(shares) >= SLOPE_SKIP + 21 else shares[:-SLOPE_SKIP]
        bm = _median(base) if base else None
        if bm and bm > 0 and share is not None:
            slope = round(share / bm - 1.0, 4)
    mom = None
    if len(closes) > MOM_WINDOW and closes[-1 - MOM_WINDOW] > 0:
        mom = round((closes[-1] / closes[-1 - MOM_WINDOW] - 1.0) * 100, 2)
    breadth = round(sum(breadths[-BREADTH_WINDOW:]) / min(BREADTH_WINDOW, len(breadths)), 4) \
        if len(breadths) >= 5 else None
    return {
        'share': share, 'share_pctile': share_pctile, 'share_med250': share_med,
        'amount_stability': stability, 'share_slope20': slope,
        'member_count': day_rows[-1]['member_count'], 'member_delta': member_delta,
        'margin_crowd': margin, 'mom60': mom, 'breadth20': breadth,
        'div_yield_med': div_yield, 'div_coverage': div_cov,
        'mv_share': mv_share, 'mv_coverage': mv_cov,
        'sector_name': day_rows[-1]['sector_name'], 'sector_type': day_rows[-1]['sector_type'],
    }


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0, 6)


def _percentile_of(universe: list[dict], key: str) -> dict[str, float | None]:
    """把各板块的原始读数换成**当日横截面分位**（0~1）。缺项仍是 None，不参与排名。"""
    vals = [d[key] for d in universe if d.get(key) is not None]
    out: dict[str, float | None] = {}
    for d in universe:
        v = d.get(key)
        out[d['sector_code']] = None if v is None else _pct_rank(vals, v)
    return out


def compute_day(target: dt.date, *, sector: str | None = None, min_rows: int = 60) -> list[dict]:
    """算一个交易日的所有板块读数。返回行列表（还没写库）。"""
    sessions = _sessions(target, WINDOW)
    if not sessions:
        return []
    lo = sessions[0]
    rows = _sector_daily(lo, target, sector)
    grouped = _series(rows)
    yields = _dividend_yields(target)
    mvs, all_mv = _market_values(target)
    margins = _margin_ratio(target)
    members = _members_at(target)
    early_counts = _member_counts(sessions[0])
    late_counts = _member_counts(target) if target != sessions[0] else early_counts

    out: list[dict] = []
    for code, day_rows in grouped.items():
        if len(day_rows) < min_rows:
            continue          # 一个板块在窗口里只有十几天数据 → 算不出分位与稳定性，整行不要
        day_rows = day_rows[-WINDOW:]
        mem_codes = members.get(code) or []
        early_n, late_n = early_counts.get(code), late_counts.get(code)
        member_delta = (late_n - early_n) if (early_n and late_n) else None
        div_vals = [yields[c] for c in mem_codes if c in yields]
        div_cov = round(len(div_vals) / len(mem_codes), 3) if mem_codes else None
        mv_vals = [mvs[c] for c in mem_codes if c in mvs]
        mv_cov = round(len(mv_vals) / len(mem_codes), 3) if mem_codes else None
        marg_vals = [margins[c] for c in mem_codes if c in margins]
        rec = _roll_up(day_rows,
                       member_delta=member_delta,
                       div_yield=_median(div_vals) if div_vals else None,
                       div_cov=div_cov,
                       mv_share=(round(sum(mv_vals) / all_mv, 6) if mv_vals and all_mv else None),
                       mv_cov=mv_cov,
                       margin=_median(marg_vals) if marg_vals else None)
        rec['sector_code'] = code
        rec['trade_date'] = target
        out.append(rec)
    return _score_all(out, target)


def _score_all(recs: list[dict], target: dt.date) -> list[dict]:
    """横截面分位 → 三个分 → 阶段与象限。分成一步是因为**分位必须有母体**：
    单独算一个板块得不到"它在今天所有板块里的位置"。"""
    for key in ('div_yield_med', 'amount_stability', 'share_med250', 'mv_share',
                'share_pctile', 'share_slope20', 'member_delta', 'margin_crowd',
                'mom60', 'breadth20'):
        pct = _percentile_of(recs, key)
        for r in recs:
            r[key + '_p'] = pct.get(r['sector_code'])

    for r in recs:
        toll, t_missing = score_from_parts(
            {'div_yield': r['div_yield_med_p'], 'stability': r['amount_stability_p'],
             'share_med': r['share_med250_p'], 'mv_share': r['mv_share_p']}, TOLL_W)
        crowd, c_missing = score_from_parts(
            {'share_pctile': r['share_pctile_p'], 'inflow': r['share_slope20_p'],
             'member_delta': r['member_delta_p'], 'margin': r['margin_crowd_p']}, CROWD_W)
        imagine, i_missing = score_from_parts(
            {'mom': r['mom60_p'], 'breadth': r['breadth20_p'], 'heat': r['share_pctile_p'],
             'concept': 1.0 if r['sector_type'] == '概念' else 0.0}, IMAGINE_W)
        r['toll_score'], r['crowding_temp'], r['imagine_score'] = toll, crowd, imagine
        miss = sorted(set(t_missing + c_missing + i_missing))
        r['missing'] = ','.join(miss) if miss else None
        r['stage'], r['stage_reason'] = judge_stage(
            share_pctile=r['share_pctile'], share_slope20=r['share_slope20'],
            crowding_temp=crowd, mom60=r['mom60'], member_delta=r['member_delta'])
    tm = _median([r['toll_score'] for r in recs if r['toll_score'] is not None])
    im = _median([r['imagine_score'] for r in recs if r['imagine_score'] is not None])
    for r in recs:
        r['quadrant'] = quadrant_of(toll=r['toll_score'], imagine=r['imagine_score'],
                                    toll_median=tm, imagine_median=im)
    ordered = sorted([r for r in recs if r['toll_score'] is not None],
                     key=lambda x: -x['toll_score'])
    for i, r in enumerate(ordered, 1):
        r['toll_rank'] = i
    hot = sorted([r for r in recs if r['crowding_temp'] is not None],
                 key=lambda x: -x['crowding_temp'])
    for i, r in enumerate(hot, 1):
        r['crowding_rank'] = i
    for r in recs:
        r['universe'] = len(recs)
        r['spec_version'] = SPEC_VERSION
    return recs


_UPSERT = """
insert into analytics.sector_allocator
 (sector_code, trade_date, spec_version, sector_name, sector_type, universe,
  share, share_pctile, share_med250, amount_stability, div_yield_med, div_coverage,
  mv_share, mv_coverage, toll_score, toll_rank, share_slope20, member_count, member_delta,
  margin_crowd, crowding_temp, crowding_rank, mom60, breadth20, stage, stage_reason,
  imagine_score, quadrant, missing, calculated_at)
values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
on conflict (sector_code, trade_date, spec_version) do update set
  sector_name = excluded.sector_name, sector_type = excluded.sector_type,
  universe = excluded.universe, share = excluded.share, share_pctile = excluded.share_pctile,
  share_med250 = excluded.share_med250, amount_stability = excluded.amount_stability,
  div_yield_med = excluded.div_yield_med, div_coverage = excluded.div_coverage,
  mv_share = excluded.mv_share, mv_coverage = excluded.mv_coverage,
  toll_score = excluded.toll_score, toll_rank = excluded.toll_rank,
  share_slope20 = excluded.share_slope20, member_count = excluded.member_count,
  member_delta = excluded.member_delta, margin_crowd = excluded.margin_crowd,
  crowding_temp = excluded.crowding_temp, crowding_rank = excluded.crowding_rank,
  mom60 = excluded.mom60, breadth20 = excluded.breadth20,
  stage = excluded.stage, stage_reason = excluded.stage_reason,
  imagine_score = excluded.imagine_score, quadrant = excluded.quadrant,
  missing = excluded.missing, calculated_at = now()
"""


def write(rows: list[dict]) -> int:
    batch = [(r['sector_code'], r['trade_date'], r['spec_version'], r.get('sector_name'),
              r.get('sector_type'), r.get('universe'), r.get('share'), r.get('share_pctile'),
              r.get('share_med250'), r.get('amount_stability'), r.get('div_yield_med'),
              r.get('div_coverage'), r.get('mv_share'), r.get('mv_coverage'),
              r.get('toll_score'), r.get('toll_rank'), r.get('share_slope20'),
              r.get('member_count'), r.get('member_delta'), r.get('margin_crowd'),
              r.get('crowding_temp'), r.get('crowding_rank'), r.get('mom60'),
              r.get('breadth20'), r.get('stage'), r.get('stage_reason'),
              r.get('imagine_score'), r.get('quadrant'), r.get('missing')) for r in rows]
    if batch:
        engine.executemany(_UPSERT, batch, role='etl')
    return len(batch)


def run(target: dt.date | None = None) -> dict:
    from app.tdx_client.readiness import expected_trade_date
    d = target or expected_trade_date()
    recs = compute_day(d)
    n = write(recs)
    return {'date': str(d), 'sectors': n, 'spec_version': SPEC_VERSION}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='分配者视角四块读数（判据见 docs/分配者视角工作指南.md）')
    ap.add_argument('--date', help='目标交易日（默认取最近一个已收盘交易日）')
    ap.add_argument('--since', help='从这一场开始逐日补算（含）')
    ap.add_argument('--limit-sectors', type=int, help='只算某个板块（试形状用，传板块代码）')
    args = ap.parse_args(argv)

    if args.since:
        lo = dt.date.fromisoformat(args.since)
        hi = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
        days = [r['trade_date'] for r in engine.query(
            "select distinct trade_date from market_data.sector_daily "
            "where trade_date between %s and %s order by 1", (lo, hi))]
    else:
        days = [dt.date.fromisoformat(args.date)] if args.date else None
        if days is None:
            from app.tdx_client.readiness import expected_trade_date
            days = [expected_trade_date()]
    total = 0
    for d in days:
        out = run(d)
        total += out['sectors']
        print('[%s] 写入 %d 个板块（spec %s）' % (out['date'], out['sectors'], out['spec_version']),
              flush=True)
    print('合计 %d 行' % total)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
