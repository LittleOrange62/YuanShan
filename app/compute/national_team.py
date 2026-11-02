# -*- coding: utf-8 -*-
"""算 `analytics.national_team_etf_daily`：国家队代理指标（两把尺的 ETF 份额/规模/净申赎/占比）。

用户 2026-10-26 点名要做的是「头部沪深300基金市值 ÷ 沪深300流通市值」，并且
**「四大 和 全部 都计算一次，但凡有一个报警或有风险或低估都提示」** ⇒ 这里两把尺各落一行：

    big4 = 510300 华泰柏瑞 + 510310 易方达 + 510330 华夏 + 159919 嘉实（媒体常引的那一组）
    all  = 全部**被动**跟踪沪深300的场内 ETF（清单见 `PASSIVE_HS300`，人工审过）

为什么"全部"这把不能用名字正则（2026-10-26 现查最近一场 1,694 只份额名单时撞到的）：
`name ~ '沪深300|300ETF'` 会把这些一起捞进来，而它们**不是**同一把尺 ——
    517300 沪港深300ETF（跟踪沪港深300，不是沪深300）
    159836 创业板300ETF（另一个指数）
    561300 / 562070 / 561990 沪深300**增强**/指增（主动增强，仓位与跟踪误差都不是一回事）
    562310 / 159656 沪深300**成长**、512530 沪深300**红利**、562170 沪深300**现金流**（Smart beta 因子）
所以清单是显式的，并配 `lint_missing_members()`：**每次跑都把"名字像却没进清单"的代码报出来**，
新上市的 300ETF 不会被静默漏掉，也不会把增强型偷偷算进来。

三个口径上的硬规定（都是踩过的坑，不是风格）：
1. **净申赎走 `market_data.view_etf_flow` 的分区 LAG**，不在这里 `今日 − 昨日` 自己减：
   新 ETF 成立首日那笔是首发规模不是申购（视图注释实测 2026-08-26 深交所 4 只新 ETF +11.31 亿份，
   把真实的 −28.05 亿份冲成 −16.73 亿份，失真 40%）。视图里首日 delta 自然为 NULL，本模块只加总有值的。
2. **分母（流通市值）＝用当日日线自算**（nt-2，2026-10-31 换的这把尺）：
   每票流通股本＝当日成交量 ÷ 当日换手率，× 当日**未复权**收盘＝该票当日流通市值，
   再对 `reference_data.sector_member` 的 ZS_沪深300 成分求和。
   为什么换：nt-1 那把尺的分母来自 `market_data.stock_snapshot.float_market_value_yuan`，
   而那张快照表最早只有 **2026-07-22**（现查 2026-10-31：49 天）⇒ 679 场里只有 **47 场**有占比，
   用户点名的「图表做的连贯一点」量的就是这个 —— 不是画法问题，是分母没有底。
   换尺前后对账（同日两把尺都有数的 2026-10-22~10-29 六天，成分 299~300 只）：
   日线自算 48.007/48.482/48.761/48.283/48.537/48.436 万亿 vs 快照求和 48.096/48.574/48.856/48.378/48.631/48.560 万亿
   ⇒ 差 **−0.186% ~ −0.256%**，与当初「快照 vs 东财 −0.08%」同一量级（差额＝那天有一两只成分停牌或没给换手率，
   少算那一只的市值；这一天的覆盖数就落在 `denom_covered`/`denom_members` 两列里，缺了要能说出来）。
   ⚠️ 这把尺**不是**插值也不是外推：用的就是那一天自己的成交、换手、收盘三个原始读数。
   ⚠️ 总市值那一把（`total_mv_yuan`/`ratio_total_pct`）**仍然只从快照来** —— 总股本日线里没有那一列
   （换手率只覆盖流通股本），所以备用尺照旧只有几十天有数，界面上必须如实报覆盖，不与主尺混成一条线。
   ⚠️ 东财那一发**只当对照**（这台机器对 push2 有频率封，不能让它当主尺）。
   nt-1 那 47 行不动（主键含 spec_version ⇒ 新尺写新行，两批可以并排对账）。
3. **异动与历史分档不落这张表**：那是阈值型判据，阈值一改历史行就挂旧含义。
   读侧 `duanxian/national_team.py` 按当次窗口现算并把阈值/分位随接口交出。

用法：
    python -m app.compute.national_team                      # 库里最近一场起算，往前 40 场
    python -m app.compute.national_team --start 20240101 --end 20261027
    python -m app.compute.national_team --start 20261027 --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from app.db import engine

SPEC = 'nt-2'          # 10-31 分母换尺（快照求和 → 当日日线自算），nt-1 那 47 行留着对账
INDEX_CODE = '000300.SH'
MEMBER_SECTOR = 'ZS_沪深300'          # 通达信插件库搬进来的沪深300成分（300 只）

#: big4：媒体说"国家队借道沪深300ETF"通常点的这四只
BIG4 = ['510300.SH', '510310.SH', '510330.SH', '159919.SZ']

#: all：全部被动跟踪沪深300的场内 ETF（2026-10-26 按份额名单人工审过）。
#: 515350/515360 是自检抓进来的一轮（名字就是「沪深300ETF」，只是份额排在后面，第一版名单漏了）。
PASSIVE_HS300 = sorted(set(BIG4) | {
    '510320.SH', '510350.SH', '510360.SH', '510370.SH', '510380.SH', '510390.SH',
    '515130.SH', '515310.SH', '515330.SH', '515350.SH', '515360.SH', '515380.SH', '515390.SH',
    '515660.SH',
    '561930.SH', '563520.SH',
    '159300.SZ', '159330.SZ', '159393.SZ', '159673.SZ', '159925.SZ',
})

#: 名字里带"300"但**不是**沪深300 被动指数的那些（清单自检用，见 `lint_missing_members`）。
_NOT_HS300_PASSIVE = ('增强|指增|价值|成长|红利|现金流|低波|ESG|沪港深|深300|创业板|民企|科创')

SCALES = {'big4': BIG4, 'all': PASSIVE_HS300}

#: `market_data.fund_daily.close` 的单位是 **0.1 元（角）**，不是元 —— 乘份额前必须换算。
#: 依据（2026-10-26 实测）：① 三只 ETF 存值与公开价正好差 10 倍
#:   510300 存 41.79 / 公开 4.179，159915 存 26.51 / 2.651，512880 存 10.21 / 1.021；
#: ② 全表 `amount_yuan ÷ volume_units ÷ close` 的中位 = 0.0999，p5~p95 = 0.099~0.101
#:   （个别标的成交量按"手"给，所以②只是旁证，逐只判据靠①那类对照）。
#: ⚠️ 第一版没换算 ⇒ 规模与占比整体放大 10 倍（big4 曾算出 3.388 万亿 / 占比 6.94%，
#:   真值是 3,388 亿 / 0.694%）。这种错看数字看不出来，只能靠下面那道隐含均价自检。
PRICE_TICK_TO_YUAN = 10.0

#: 自检：规模 ÷ 份额 = 隐含均价（元）。落在这个带外就说明价格单位又变了，当场报出来。
AVG_PRICE_BAND = (0.1, 200.0)

_RATIO_SQL = """
with sh as (select s.trade_date, s.security_code, s.share_units
              from market_data.etf_share s
             where s.security_code = any(%s::text[]) and s.trade_date between %s and %s),
px  as (select f.security_code, f.trade_date, f.close
          from market_data.fund_daily f
         where f.security_code = any(%s::text[]) and f.trade_date between %s and %s),
agg as (select sh.trade_date,
                count(distinct sh.security_code)::int              as etf_count,
                sum(sh.share_units)                                as share_units,
                sum(sh.share_units * coalesce(px.close, 0))         as size_yuan_ticks,
                count(px.close)                                    as with_close
            from sh left join px using (security_code, trade_date)
           group by 1),
flow as (select v.trade_date, sum(v.delta_share_units) as net_creation_units
            from market_data.view_etf_flow v
           where v.security_code = any(%s::text[]) and v.trade_date between %s and %s
             and v.delta_share_units is not null
           group by 1)
select agg.trade_date, agg.etf_count, agg.share_units, agg.size_yuan_ticks, agg.with_close,
       flow.net_creation_units,
       (select array_agg(s2.security_code order by s2.security_code) from sh s2
         where s2.trade_date = agg.trade_date) as members
  from agg left join flow using (trade_date)
 order by 1
"""


def _float(v):
    return None if v is None else float(v)


def ratios_for(codes: list[str], start: dt.date, end: dt.date) -> list[dict]:
    return engine.query(_RATIO_SQL, (codes, start, end, codes, start, end, codes, start, end),
                        role='reader')


#: 分母＝本地自算：300 成分在快照表里的流通市值合计（一天一行）。
#: 实测 2026-10-27 = 48.38 万亿，与东财当日 48.42 万亿差 −0.08% ⇒ 可以当主尺。
_DENOM_SQL = """
select sn.trade_date,
       count(*) filter (where sn.float_market_value_yuan is not null)::int as with_fmv,
       count(distinct sn.security_code)::int                               as members,
       sum(sn.float_market_value_yuan)                                     as float_mv_yuan,
       sum(sn.total_market_value_yuan)                                     as total_mv_yuan
  from reference_data.sector_member m
  join market_data.stock_snapshot sn using (security_code)
 where m.sector_code = %s and sn.trade_date between %s and %s
 group by 1
"""


def denominator(start: dt.date, end: dt.date) -> dict[str, dict]:
    rows = engine.query(_DENOM_SQL, (MEMBER_SECTOR, start, end), role='reader')
    return {str(r['trade_date']): r for r in rows}


#: nt-2 的主尺：分母＝当日日线自算（见模块 docstring 第 2 条）。
#: 三个读数全来自那一天自己：`close`（未复权）、`volume_shares`、`turnover_rate_percent`。
#: 换手率没给或成交量为 0（停牌、那天没有这根线）的那只**不进和**，但计进 `members_total`
#: ⇒ `with_implied < members_total` 就是"这一天的分母少算了 N 只"，落进 `missing` 并上屏。
#: ⚠️ 这里刻意写成 `security_code = any(%s::text[])` 而不是 join `sector_member`：
#:   2026-10-31 实测 join 那一版在全史范围上顶穿 reader 的 60 秒 statement timeout（连 6 个月都超时），
#:   走 `(security_code, trade_date)` 主键的这一版全史 683 天实测 25.6 秒。名单在 Python 侧取一次即可。
_DENOM_DAILY_SQL = """
select d.trade_date,
       count(*)::int                                                  as members_total,
       count(*) filter (where d.turnover_rate_percent > 0
                          and d.volume_shares > 0 and d.close > 0)::int as with_implied,
       sum(d.close * (d.volume_shares / (d.turnover_rate_percent / 100.0)))
         filter (where d.turnover_rate_percent > 0
                     and d.volume_shares > 0 and d.close > 0)          as float_mv_yuan
  from market_data.stock_daily d
 where d.trade_date between %s and %s
   and d.security_code = any(%s::text[])
 group by 1
 order by 1
"""


def member_codes() -> list[str]:
    """当前这份 ZS_沪深300 成分名单（库里没有时点化的成分表，与 nt-1 用的是同一份，见 docstring）。"""
    rows = engine.query(
        'select distinct security_code from reference_data.sector_member where sector_code = %s',
        (MEMBER_SECTOR,), role='reader')
    return sorted(r['security_code'] for r in rows)


def denominator_daily(start: dt.date, end: dt.date, codes: list[str] | None = None) -> dict[str, dict]:
    rows = engine.query(_DENOM_DAILY_SQL, (start, end, codes or member_codes()), role='reader')
    return {str(r['trade_date']): r for r in rows}


def lint_denominator(daily: dict[str, dict], snap: dict[str, dict]) -> list[str]:
    """两把尺都有数的那几天，逐日比一次自算/快照，差出带外就报出来。

    这道闸是替"换尺"这件事自己准备的：新尺的绝对值没有第二条独立来源可查，
    唯一的锚就是"它与旧尺在同一天的差不该超过千分之几"。差大了只有两种可能
    —— 换手率的单位变了，或者成分名单/日线本身漏了一天 ⇒ 都要当场出声，不能让它静默变成一个更小的占比。
    """
    bad = []
    for day, dn in sorted(daily.items()):
        sn = snap.get(day) or {}
        a, b = _float(dn.get('float_mv_yuan')), _float(sn.get('float_mv_yuan'))
        if not a or not b:
            continue
        diff = (a / b - 1) * 100.0
        if abs(diff) > 1.0:
            bad.append(f'{day} 自算 {a / 1e12:.3f} 万亿 vs 快照 {b / 1e12:.3f} 万亿 差 {diff:+.2f}%')
    return bad


def lint_missing_members(day: dt.date) -> list[str]:
    """名字像沪深300**被动**ETF、当天有份额行、却不在清单里的代码 ⇒ 报出来给人看。

    两道筛都不许省：只按名字收会把 沪港深300 / 创业板300 / 300增强 / 300价值 / ESG300 / 深300
    一起捞进来（2026-10-26 现查这份名单时它们就在里面，实测一次报出 23 条噪音）；
    加了 `_NOT_HS300_PASSIVE` 之后剩下的才是"真的该收没收到"，报 0 条才算干净。
    新上市的 300ETF 不会被静默漏掉，也不会把增强型偷偷算进来。
    """
    rows = engine.query(
        """select s.security_code, s.name
             from market_data.etf_share e join reference_data.security s using (security_code)
            where e.trade_date = %s
              and s.asset_type = 'etf'
              and s.name ~ '沪深300|300ETF|HS300'
              and s.name !~ %s
              and s.security_code <> all(%s::text[])
            group by 1, 2 order by 1""",
        (day, _NOT_HS300_PASSIVE, PASSIVE_HS300), role='reader')
    return [f"{r['security_code']} {r['name']}" for r in rows]


def build_rows(start: dt.date, end: dt.date) -> list[tuple]:
    snap = denominator(start, end)
    daily = denominator_daily(start, end)
    out = []
    for scale, codes in SCALES.items():
        for r in ratios_for(codes, start, end):
            day = str(r['trade_date'])
            dnv = daily.get(day) or {}
            snv = snap.get(day) or {}
            # 主尺分母：日线自算，全史都有底；旧尺（快照）只用于对照与 lint
            float_mv = _float(dnv.get('float_mv_yuan'))
            total_mv = _float(snv.get('total_mv_yuan'))
            covered = dnv.get('with_implied')
            members = dnv.get('members_total')
            shares = _float(r.get('share_units'))
            ticks = _float(r.get('size_yuan_ticks'))
            size = None if ticks is None else ticks / PRICE_TICK_TO_YUAN
            missing = []
            if not dnv:
                missing.append('float_mv_yuan')          # 那天成分一根日线都没有
            elif float_mv is None:
                missing.append('float_mv_yuan.no_implied')  # 有日线但换手率/量全没给 ⇒ 反推不出股本
            elif covered is not None and members is not None and covered < members:
                # 码保持原样（它是前端那张中文表的键，带上数字就查不到了）；少算了哪几只、共几只，
                # 交在 `denom_covered`/`denom_members` 两列里，界面按数字念出来。
                missing.append('float_mv_yuan.partial')
            if size is not None and r.get('with_close') != r.get('etf_count'):
                missing.append('size_yuan.partial')
            # 隐含均价越带 = 价格单位又变了（这一道闸就是替第一版那个 10 倍错准备的）
            if size is not None and shares:
                avg = size / shares
                if not AVG_PRICE_BAND[0] <= avg <= AVG_PRICE_BAND[1]:
                    missing.append(f'size_yuan.avg_price_out_of_band({avg:.3f})')
            out.append((
                r['trade_date'], scale, SPEC, r['etf_count'],
                _float(r.get('share_units')), size, _float(r.get('net_creation_units')),
                float_mv,
                None if (size is None or not float_mv) else round(size / float_mv * 100, 4),
                total_mv,
                None if (size is None or not total_mv) else round(size / total_mv * 100, 4),
                json.dumps(r.get('members') or [], ensure_ascii=False),
                ','.join(missing) or None,
                'daily_derived' if float_mv is not None else None,
                covered, members,
            ))
    return out


COLS = ['trade_date', 'scale_key', 'spec_version', 'etf_count', 'share_units', 'size_yuan',
        'net_creation_units', 'float_mv_yuan', 'ratio_float_pct', 'total_mv_yuan',
        'ratio_total_pct', 'members', 'missing', 'denom_source', 'denom_covered', 'denom_members']


def write(rows: list[tuple], start: dt.date, end: dt.date) -> int:
    if not rows:
        return 0
    engine.execute(f"DELETE FROM analytics.national_team_etf_daily "
                   f"WHERE trade_date between %s and %s and spec_version = %s",
                   (start, end, SPEC), role='etl')
    return engine.copy_rows('analytics.national_team_etf_daily', COLS, iter(rows), role='etl')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', help='YYYYMMDD')
    ap.add_argument('--end', help='YYYYMMDD')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.end is None:
        args.end = str(engine.scalar('SELECT max(trade_date) FROM market_data.etf_share'))
    if args.start is None:
        args.start = str(engine.scalar(
            'SELECT min(trade_date) FROM (SELECT DISTINCT trade_date FROM market_data.etf_share '
            ' ORDER BY trade_date DESC LIMIT 40) t'))
    start = dt.datetime.strptime(args.start, '%Y%m%d').date()
    end = dt.datetime.strptime(args.end, '%Y%m%d').date()

    rows = build_rows(start, end)
    daily = denominator_daily(start, end)
    snap = denominator(start, end)
    print(f'=== {start} .. {end}：两把尺各 {len(rows) // 2} 场，共 {len(rows)} 行 ===')
    bad = [r for r in rows if (r[12] or '').find('avg_price_out_of_band') >= 0]
    print(f'价格单位自检（隐含均价落在 {AVG_PRICE_BAND[0]}~{AVG_PRICE_BAND[1]} 元之外）：'
          f'{len(bad)} 行' + ('' if not bad else f'，例：{bad[0][0]} {bad[0][1]} {bad[0][12]}'))
    newest = sorted({r[0] for r in rows})[-1:]
    if newest:
        print('清单自检（名字像却没进清单的代码，0 条才算干净）：', lint_missing_members(newest[0]) or '无')
    for r in sorted(rows, key=lambda x: (str(x[0]), x[1]), reverse=True)[:6]:
        ratio = '—' if r[8] is None else f'{float(r[8]):.3f}%'
        print(f'  {r[0]} {r[1]:5s} {r[3]:2d} 只  份额 {float(r[4] or 0) / 1e8:9.2f} 亿份  '
              f'规模 {(float(r[5] or 0) / 1e12):6.3f} 万亿  分母 '
              f'{"—" if r[7] is None else format(float(r[7]) / 1e12, ".2f") + " 万亿"}  '
              f'占比 {ratio}  净申赎 {r[6] if r[6] is None else format(float(r[6]) / 1e8, "+.2f") + " 亿份"}')
    with_ratio = sum(1 for r in rows if r[8] is not None)
    print(f'有占比的那 {with_ratio} 行 / 共 {len(rows)} 行'
          f'（分母＝当日日线自算，见 docstring 第 2 条；没有的那一天一根成分日线都没取到）')
    part = [r for r in rows if 'float_mv_yuan.partial' in (r[12] or '')]
    print(f'分母少算了票的那些行（换手率或量没给、当天停牌的成分）：{len(part)} 行'
          + ('' if not part else f'，例：{part[0][0]} {part[0][1]} {part[0][12]}'))
    drift = lint_denominator(daily, snap)
    print(f'两把尺同日对账（|自算−快照| > 1% 才报，实测这批 −0.19%~−0.26%）：{len(drift)} 天'
          + ('' if not drift else '，前两条：' + '；'.join(drift[:2])))
    if args.dry_run:
        print('--dry-run，未写库')
        return 0
    print('写入行数：', write(rows, start, end))
    return 0


if __name__ == '__main__':
    sys.exit(main())
