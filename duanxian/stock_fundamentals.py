# -*- coding: utf-8 -*-
"""「个股深度复盘 · 财务多期与外推」读侧：只读 `reference_data.stock_financial_period`，不算判据。

判据（加权外推那三条）只住 `app/compute/financial_forecast.py` 一处；这一层做四件：
1. 定这一只要不要得到数（表空 / 这只票没行 / 只有一档），三种情况说的话不一样；
2. 把金额统一成亿元交出去（源是元，逐期 20 行 × 三个金额，让前端各除一遍迟早有人除错）；
3. 按 `fetched_at` 说清这份财务有多旧（本机只有一个源，接口断供时这里就是停在最后一场）；
4. 把 caveat 与字段义写成句子交出去，页面只念不编。

三条不许破：
  · 缺的读数给 `null` 不给 0（0 会被读成"这一季营收为零"）；
  · 外推不是分析师预期、不是买卖依据，屏上必须并排着说；
  · 「累计档」与「单季档」不可混用 ⇒ 每一块都自带 `period_kind`，前端不许把两档并到一张折线图里。
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List, Optional

from app.db import engine

from .quant import _fail, _num, _one, jsonable

TABLE = 'reference_data.stock_financial_period'
YI = 1e8

CALIBER = (
    '源＝同花顺财务摘要（`ingest/jobs/c25_financials_ths.py`，本机唯一一份财务源；'
    '东财版只做对账不入库，避免同一件事两个数）。'
    '两档都是源直接给的：`cum`＝年初到报告期累计（年报就看这一档），'
    '`single`＝本季度单季（季节性、环比看这一档）；两档不可混用，同比也是各档各自的。'
    '金额一律亿元、比率一律百分数、每股类是元/股。'
    '外推三条：① 下一季＝上一年的同一季 × (1 + 近 4 季单季同比的半衰加权) 并用近 3 年同季季节环比校验；'
    '② 当年＝已披露累计原样保留，未披露的各季按 ① 逐季补（Q4 单独一档：分母是同年前三季均值）；'
    '③ 明年＝0.4×近 5 年年报 CAGR + 0.6×近 3 年年报 CAGR，情景带取近 5 年年报同比的 25/75 分位。'
    '半衰期 2 季、增速夹在 −60%~+150%、两法分歧超过 25% 就不给单一数。'
)

DEFINITIONS: Dict[str, str] = {
    'period': '报告期（季末日期）。这是报告期，不是披露日 —— 4 月底才披露完的年报，'
              '在 3 月就已经是「最新一期」了。',
    'revenue': '营业总收入（亿元）。',
    'revenue_yoy': '同比 %：我们按「上一年的同一季/同一年」自己重算的一份。',
    'revenue_yoy_src': '同比 %：源给的那一份。两列并排留是为了能当面比，'
                       '不是有一边算错 —— 累计档里源那个同比是「对去年累计」。',
    'revenue_qoq': '环比 %：对上一季。Q1 的环比基期是上年 Q4，跨年可比性有限，看趋势优先用同比。',
    'net_profit': '净利润（亿元，含少数股东）。',
    'deduct_profit': '扣非净利润（亿元）：剔一次性损益，看主营更干净。缺了就是缺（新股早年常不披露）。',
    'gross_margin': '销售毛利率 %（单季档，源给）。⚠️ 绝对值超过 1000% 的那一格按 null 交'
                    '（那是基期接近 0 时的算术，不是比率），库里照源存。',
    'net_margin': '销售净利率 %（单季档，源给）。同毛利率：超 1000% 按 null 交，不摆天文数字。',
    'roe_single': '净资产收益率 % —— ⚠️ 这是单季口径，不能与年报上那个 ROE 比高低；'
                  '累计档的 ROE 是年内累计（Q1 的 10% 与全年的 10% 不是一个尺子）。'
                  '超 1000% 的那一格同样按 null 交。',
    'next_quarter': '下一季的预测：`value` 只有两条腿不打架时才有；'
                    '`method_a`/`method_b` 两条都在，`divergence` 是两法相对差（百分点）。',
    'this_year': '当年年报：`reported_through` 是已披露到第几期、`disclosed` 是这一年已披露几期，'
                 '`missing_quarters` 是补上去的那几季（每季各带自己的依据；补不下去的那一季带 '
                 '`fail_reason` 且值为 null）。已披露部分原样保留，不是预测。',
    'next_year': '明年年报：`growth_percent` 是加权 CAGR（`cagr_basis` 说这一只是几条腿算的），'
                 '`scenario_low/high` 是情景带（近 `scenario_years_used` 份年报同比的 25/75 分位'
                 '与点估计取并），一定包含点估计，不是置信区间。'
                 '`years_used` 是这只票在本机一共有几份年报，不是用来取分位的那几份。',
    'reason': '为什么这一格没数。写的是事实（期数不足／基期缺／两法分歧大），不是「预计持平」。',
    'fetched_at': '这份财务最后一次采集的时间。源断供时这里就停在最后一场 —— 只报「多旧」，不猜当期。',
    'quarters_available': '这只票在本机有几个单季（<8 就谈不上季节性，<12 同比样本也薄）。',
}


def _yi(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(x) else round(x / YI, 4)


def _moneyify(d: Dict[str, Any], keys) -> Dict[str, Any]:
    out = dict(d)
    for k in keys:
        if k in out:
            out[k] = _yi(out[k])
    for sub in ('method_a', 'method_b'):
        if isinstance(out.get(sub), dict) and 'value' in out[sub]:
            out[sub] = dict(out[sub], value=_yi(out[sub]['value']))
    for k in ('band_low', 'band_high', 'scenario_low', 'scenario_high'):
        if k in out:
            out[k] = _yi(out[k])
    for q in out.get('missing_quarters') or []:
        if isinstance(q, dict) and 'value' in q:
            q['value'] = _yi(q['value'])
    return out


def load_rows(code: str) -> List[Dict[str, Any]]:
    return engine.query(
        f'select * from {TABLE} where security_code = %s order by report_date desc',
        (code,), role='reader') or []


def board(code: str) -> dict:
    """一只要的全部财务读数。不可用时只交 available/reason，不带 rows 键。"""
    code = (code or '').strip().upper()
    if not code:
        return _fail('财务多期', TABLE, None, '没交代码')
    n = _one(f'select count(*)::int n from {TABLE} where security_code = %s', (code,))
    if not n or not n.get('n'):
        exist = _one('select count(*)::int n from reference_data.security where security_code = %s', (code,))
        why = ('这只票主档里有，但本机一次都没采过财务 —— 跑 '
               f'`python -m ingest.jobs.c25_financials_ths --code {code}`'
               if exist and exist.get('n') else '主档里没有这个代码（先确认代码带没带市场后缀）')
        return _fail('财务多期', TABLE, None, why)

    rows = load_rows(code)
    kinds = {r.get('period_kind') for r in rows}
    fresh = _one(f'select max(fetched_at)::text t, max(report_date)::text d from {TABLE} '
                 f'where security_code = %s', (code,)) or {}
    quarters = [r for r in rows if r.get('period_kind') == 'single']

    from app.compute import financial_forecast as F        # 延迟 import：判据只在那一处
    table = F.period_table(rows)
    fc = F.forecasts(rows)

    for row in table['quarters'] + table['annual']:
        for k in ('revenue', 'net_profit', 'deduct_profit'):
            row[k] = _yi(row.get(k))
    for name in list(fc):
        blk = fc[name]
        fc[name] = {'next_quarter': _moneyify(blk['next_quarter'], ('value',)),
                    'this_year': _moneyify(blk['this_year'], ('forecast_value',)),
                    'next_year': _moneyify(blk['next_year'], ('value',))}

    age_h = None
    if fresh.get('t'):
        try:
            got = dt.datetime.fromisoformat(str(fresh['t']))
            age_h = round((dt.datetime.now(got.tzinfo) - got).total_seconds() / 3600.0, 1)
        except ValueError:
            age_h = None

    caveats = [
        {'code': 'not_analyst', 'text':
         '这三格是本机按公开历史做的加权外推，不是券商一致预期、不是公司公告、'
         '更不是买卖依据。历史里没出现过的那种变化（政策、大额减值、并表口径调整）外推看不出来。'},
        {'code': 'q4_coarser', 'text':
         '补「当年」时 Q4 单独一档（分母是同年前三季均值，不是 Q3）。理由：很多公司 Q4 就是'
         '「全年减前三季」，且常年含减值与年终费用 —— 用 Q1..Q3 的季节形状外推 Q4 会系统性偏乐观，'
         '所以带 Q4 的那一格比别的粗。'},
        {'code': 'single_only_two', 'text':
         '两法（同比外推 / 季节环比）分歧超过 25% 时不给一个数，只给两法各自的值。'
         '分歧本身就是结论：近季增速与这只票历年这一季的季节形状在打架。'},
        {'code': 'roe_kind', 'text':
         'ROE 逐期那一列用的是单季口径，不能跟年报上的 ROE 比高低；'
         '累计档的 ROE 是年内累计（Q1 的 10% 与全年的 10% 不是一个尺子）。'},
        {'code': 'one_source', 'text':
         '本机只有同花顺这一份财务源（东财版只做对账、不入库）。源断供时这里就停在最后一场，'
         '屏上按「多久没更新」说，不拿旧值冒充当期。'},
    ]
    if age_h is not None and age_h > 96:
        caveats.append({'code': 'stale', 'text':
                        f'这份财务已 {age_h / 24:.1f} 天没重采（自选/持仓才每天刷，全市场要显式跑 '
                        f'`--all`）。披露季里这个年龄可能已经跨过一期。'})
    if table.get('suppressed_source_ratios'):
        caveats.append({'code': 'source_ratio_absurd', 'text':
                        f'源给的比率里有 {table["suppressed_source_ratios"]} 格超出了这把尺子'
                        '（毛利率/净利率/ROE 的绝对值 >1000%，或源同比 >±1 万%）—— 那是基期接近 0 时的算术，'
                        '不是这只票的读数，屏上按缺数交 null。库里照源存着（`raw` 那列有原文），'
                        '外推用的是我们自己按同季重算的同比，从来没拿这一列。'})
    if len(quarters) < 12:
        caveats.append({'code': 'thin', 'text':
                        f'这只票本机只有 {len(quarters)} 个单季（<12 ⇒ 凑不满 3 年季节性），'
                        '下面凡是靠历史外推的格子都会写「判不了」，不会硬给一个数。'})
    if 'single' not in kinds:
        caveats.append({'code': 'no_single', 'text':
                        '本机只有累计档、没有单季档（源那一发没拿到）⇒ 环比/季节性全判不了，'
                        '逐季表会是空的，这不是「这只票没季报」。'})
    return jsonable({
        'available': True,
        'code': code,
        'unit': '亿元（金额类）；比率类是百分数；每股类是元/股',
        'period_kind_note': 'cum＝年初到报告期累计；single＝本季度单季。两档不可混用。',
        'latest_report_date': fresh.get('d'),
        'fetched_at': fresh.get('t'), 'fetched_age_hours': age_h,
        'quarters_available': len(quarters),
        'rows_available': int(n.get('n') or 0),
        'kinds': sorted(k for k in kinds if k),
        'quarters': table['quarters'],
        'annual': table['annual'],
        'forecast': fc,
        'caliber': CALIBER,
        'definitions': DEFINITIONS,
        'caveats': caveats,
    })
