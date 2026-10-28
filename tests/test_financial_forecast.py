# -*- coding: utf-8 -*-
"""财务多期与三条加权外推（`app/compute/financial_forecast.py`）+ 读侧（`duanxian/stock_fundamentals.py`）。

判据层全是纯函数，单测直接喂合成序列（不碰库）；末尾两条**不打桩**，现读真库那份财务。
钉的是这几件事，都是界面上最容易说假话的地方：
  · 缺数给 None，不给 0（0 会被读成「这一季营收为零」）；
  · 两法分歧时**不给单一数**；
  · 补「当年」时已披露的部分原样保留，`disclosed` 这个计数必须等于真的期数；
  · 明年的情景带是**近 5 份**年报同比的分位（不是全部可得年报），且一定包含点估计；
  · Q4 单独一档：分母是同年前三季均值。
"""
from __future__ import annotations

import datetime as dt
import json
import math

import pytest

from app.compute import financial_forecast as F
from duanxian import stock_fundamentals as SF


def qser(vals, start_year: int = 2020):
    """{第 i 季: 值} → 单季序列（默认 2020-03-31 起）。"""
    out = {}
    y, m = start_year, 3
    for k, v in vals.items():
        p = f'{y}-{m:02d}-' + ('31' if m in (3, 12) else '30')
        out[p] = v
        m += 3
        if m > 12:
            m = 3
            y += 1
    return out


# ------------------------------------------------------------------ 基础算术的三态

def test_半衰加权最近的那期权重最大():
    assert F.half_weighted([10.0, 0.0]) > 5.0          # [0] 是最近一期
    assert F.half_weighted([10.0, 10.0, 10.0]) == pytest.approx(10.0)
    assert F.half_weighted([]) is None
    assert F.half_weighted([None, float('nan')]) is None   # 空/NaN 不进分母，也不返回 0


def test_同比环比基期为零或为空一律_none():
    assert F.yoy(120.0, 100.0) == pytest.approx(20.0)
    assert F.yoy(120.0, 0.0) is None
    assert F.yoy(120.0, None) is None
    assert F.qoq(None, 50.0) is None
    # 分母取绝对值 ⇒ 去年是负基数时同比方向仍然可读，不是硬算成假数
    assert F.yoy(-20.0, -100.0) == pytest.approx(80.0)


def test_cagr在负基数上是_none():
    assert F.cagr(100.0, 200.0, 4) == pytest.approx(18.92)
    assert F.cagr(-10.0, 200.0, 4) is None
    assert F.cagr(None, 200.0, 4) is None
    assert F.cagr(100.0, 200.0, 0) is None


def test_分位与中位数空表给_none():
    assert F.pctl([], 0.5) is None
    assert F.median([None]) is None
    assert F.pctl([10.0, 20.0, 30.0], 0.5) == pytest.approx(20.0)


# ------------------------------------------------------------------ 下一季

def test_单季不足八期就判不了这句话不能说预计持平():
    r = F.forecast_next_quarter(qser({i: 100.0 + i for i in range(7)}))
    assert r['value'] is None and r['quarters_used'] == 7
    assert '不足 8 期' in r['reason'] and '算不出来' in r['reason']
    assert r['method_a'] is None and r['method_b'] is None


def test_两法一致时给单一数并写明校验():
    # 每年同季 +10%、季节形状完全平 ⇒ A 与 B 会靠得很近
    ps = sorted(qser({i: 0 for i in range(12)}))            # 2020Q1..2022Q4 这 12 个报告期
    ser = {p: 100.0 * (1.1 ** (i // 4)) for i, p in enumerate(ps)}   # 逐年 +10%，季节形状是平的
    r = F.forecast_next_quarter(ser)
    assert r['value'] is not None and r['method_a'] and r['method_b']
    assert r['divergence'] is not None and r['divergence'] <= F.DIVERGENCE_MAX
    assert '季节环比校验' in r['basis']


def test_两法分歧超过阈值就不给单一数():
    """最后那季突然放大 9 倍：同比外推（A）说下季 250，季节环比（B）说 900 ⇒ 差 72%，只交两法各自的数。"""
    ser = qser({i: (900.0 if i == 11 else 100.0) for i in range(12)})
    r = F.forecast_next_quarter(ser)
    assert r['divergence'] is not None and r['divergence'] > F.DIVERGENCE_MAX
    assert r['value'] is None, '两法在打架却合成了一格单值'
    assert r['basis'] == '两法分歧' and '打架' in r['reason']
    assert r['method_a']['value'] and r['method_b']['value'], '分歧时两法各自的数必须都交出来'


def test_增速被夹住不让一次性损益把外推推到天上():
    assert F.growth_from_yoy([900.0, 0.0, 0.0, 0.0]) == F.GROWTH_CLAMP[1]
    assert F.growth_from_yoy([-900.0, 0.0, 0.0, 0.0]) == F.GROWTH_CLAMP[0]


def test_q4的季节比例分母是同年前三季均值():
    ser = qser({0: 100.0, 1: 100.0, 2: 100.0, 3: 400.0,      # 2020：Q4 = 前三季均值 ×4
                4: 100.0, 5: 100.0, 6: 100.0, 7: 400.0,      # 2021
                8: 100.0, 9: 100.0, 10: 100.0, 11: 400.0})   # 2022
    s = F.season_ratio(ser, '2023-12-31')
    assert s == pytest.approx(4.0), f'Q4 参照期算错：拿到 {s}（应是 Q4÷前三季均值）'
    s3 = F.season_ratio(ser, '2023-09-30')
    assert s3 == pytest.approx(1.0), 'Q1..Q3 的参照期应是上一季'


# ------------------------------------------------------------------ 当年

def test_已披露年报那一格不是预测():
    cum = {'2024-12-31': 1000.0, '2025-12-31': 1200.0}
    r = F.forecast_fiscal_year({}, cum, 2025)
    assert r['forecast_value'] == pytest.approx(1200.0)
    assert '已披露' in r['reason'] and r['basis'] == '已披露年报'


def test_补季时已披露期数这个计数要等于真的期数():
    cum = {'2025-03-31': 300.0, '2025-06-30': 600.0}
    single = qser({i: 100.0 + i * 6 for i in range(22)}, start_year=2020)   # 2020Q1..2025Q2
    r = F.forecast_fiscal_year(single, cum, 2025)
    assert r['reported_through'] == '2025-06-30'
    assert r['disclosed'] == 2, f'「已披露几期」报了 {r["disclosed"]}，与 reported_through 说的两期对不上'
    assert r['basis'] == '已披露累计 + 外推季', r['reason']
    assert len(r['missing_quarters']) == 2, [q.get('period') for q in r['missing_quarters']]
    assert all(isinstance(q, dict) and q.get('period') and q.get('value') for q in r['missing_quarters'])
    assert r['forecast_value'] > 600.0, '补完两季后的当年数应当高于已披露的 600'


def test_补不下去的那一季保持同一个形状():
    """断链时 `missing_quarters` 里不能混进裸字符串 —— 页面按 q.period 读，混形就少一行。"""
    cum = {'2025-03-31': 300.0}
    r = F.forecast_fiscal_year({}, cum, 2025)              # 单季空 ⇒ 补 6/9/12 全断
    assert r['forecast_value'] is None and r['reason']
    assert r['missing_quarters'], '断链没留痕'
    assert all(isinstance(q, dict) for q in r['missing_quarters'])
    assert r['missing_quarters'][0].get('fail_reason')


# ------------------------------------------------------------------ 明年

def test_明年的分位只用近五份年报同比():
    """全部可得年报里有早年 60% 的高增长 ⇒ 拿它取分位，带子描述的是二十年前的样子。"""
    cum = {}
    base = 100.0
    rates = [50, 50, 50, 50, 50, 8, 9, 10, 11, 12]          # 早年猛涨、近五年温和
    for i, r in enumerate(rates):
        base = base * (1 + r / 100.0)
        cum[f'{2016 + i}-12-31'] = round(base, 2)
    out = F.forecast_next_year(cum, base)
    assert out['years_used'] == 10, 'years_used 是"一共有几份年报"'
    assert out['scenario_years_used'] <= F.SCENARIO_YEARS, '分位窗口超出近 5 份的约定'
    assert out['scenario_low'] <= out['value'] <= out['scenario_high'], '情景带不含点估计'
    assert '近 5 份' in out['band_note'] or f"近 {out['scenario_years_used']} 份" in out['band_note']


def test_明年那条腿的加权是_4_与_6():
    cum = {f'{2018 + i}-12-31': 100.0 * (1.1 ** i) for i in range(6)}
    out = F.forecast_next_year(cum, 161.05)
    g5 = F.cagr(cum['2019-12-31'], cum['2023-12-31'], 4)
    g3 = F.cagr(cum['2021-12-31'], cum['2023-12-31'], 2)
    assert out['growth_percent'] == pytest.approx(round(0.4 * g5 + 0.6 * g3, 2))
    assert out['cagr_basis'] == '5年×0.4 + 3年×0.6'


def test_亏损基数时明年这一格直接判不了():
    """CAGR 在负基数上没有合法定义 ⇒ 两条腿都塌，这一格必须空着并给出原因，不能给个 0%。"""
    cum = {'2019-12-31': 100.0, '2020-12-31': -50.0, '2021-12-31': 80.0,
           '2022-12-31': 100.0, '2023-12-31': -10.0}
    out = F.forecast_next_year(cum, 120.0)
    assert out['value'] is None and out['growth_percent'] is None
    assert 'CAGR' in (out['reason'] or '')


# ------------------------------------------------------------------ 逐期表

def test_逐期表把源同比与自己重算的同比并排交出():
    rows = []
    ser = qser({i: 100.0 + i for i in range(12)})
    for p, v in ser.items():
        rows.append({'period_kind': 'single', 'report_date': dt.date.fromisoformat(p),
                     'revenue_yuan': v, 'net_profit_yuan': v * 0.3, 'deduct_profit_yuan': v * 0.28,
                     'revenue_yoy_percent': 7.7, 'gross_margin_percent': 60.0,
                     'net_margin_percent': 30.0, 'roe_percent': 5.0})
    t = F.period_table(rows, quarters=8)
    assert len(t['quarters']) == 8
    head = t['quarters'][0]
    assert head['revenue_yoy_src'] == pytest.approx(7.7), '源给的同比被丢了'
    assert head['revenue_yoy'] is not None and head['revenue_yoy'] != 7.7, '自算同比与源同比一模一样 ⇒ 重算那一列没起作用'
    assert head['revenue_qoq'] is not None


def test_逐期表只交要的那几季不乱倒序():
    rows = [{'period_kind': 'single', 'report_date': dt.date(2020, 3, 31) + dt.timedelta(days=91 * i),
             'revenue_yuan': 100.0 + i, 'net_profit_yuan': 10.0, 'deduct_profit_yuan': 9.0}
            for i in range(20)]
    t = F.period_table(rows, quarters=20)
    ps = [r['period'] for r in t['quarters']]
    assert ps == sorted(ps, reverse=True), '逐期表要按报告期倒序（最新在前），页面直接画折线'


def test_源的比率超出这把尺子时交null并报条数():
    """基期接近 0 时源能给到 3.9e7%（10-16 实测），摆上屏就是假读数；库里照存，读侧封顶成 null。"""
    rows = []
    ser = qser({i: 100.0 + i for i in range(12)})
    for i, (p, v) in enumerate(ser.items()):
        bad = i == 0
        rows.append({'period_kind': 'single', 'report_date': dt.date.fromisoformat(p),
                     'revenue_yuan': v, 'net_profit_yuan': v * 0.3, 'deduct_profit_yuan': v * 0.28,
                     'revenue_yoy_percent': 9.9e7 if bad else 7.7,
                     'gross_margin_percent': 3.9e7 if bad else 60.0,
                     'net_margin_percent': 1500.0 if bad else 30.0,     # 超 1000% 的率
                     'roe_percent': 8.0})
    t = F.period_table(rows, quarters=12)
    head = t['quarters'][0]                            # 倒序 ⇒ 最新一期是 i=11，最早那期在末尾
    oldest = t['quarters'][-1]
    assert oldest['gross_margin'] is None and oldest['net_margin'] is None, '天文数字的率没被挡掉'
    assert oldest['revenue_yoy_src'] is None, '超 ±1 万% 的源同比没被挡掉'
    assert oldest['roe_single'] == pytest.approx(8.0), '正常那一格被误杀了'
    assert head['gross_margin'] == pytest.approx(60.0) and head['revenue_yoy_src'] == pytest.approx(7.7)
    assert t['suppressed_source_ratios'] == 3, f'滤掉三格该报 3：{t["suppressed_source_ratios"]}'
    # 自算那一族不受这道闸影响（它是按同季重算的，与源的算术无关）
    by_p = {r['period']: r for r in t['quarters']}
    first_year = sorted(by_p)[4]                       # 满一年后（第二年 Q1）才有同季基期
    assert by_p[first_year]['revenue_yoy'] is not None
    assert by_p[first_year]['gross_margin'] == pytest.approx(60.0)


def test_真库那种离谱净利率上屏前被挡掉并报名字():
    """不打桩：002670.SZ 的近 20 期里有 16 期源给的净利率超 1000%（现存最大 3.89e7，10-16 现查）。"""
    d = SF.board('002670.SZ')
    assert d['available'] is True, d.get('reason')
    qs = d['quarters']
    assert len(qs) == 20, f'逐季表默认 20 期：{len(qs)}'
    bad = [r for r in qs if (r.get('net_margin') is not None and abs(r['net_margin']) > F.RATIO_ABS_MAX_PERCENT)]
    assert not bad, f'还有一格 {bad[0]["net_margin"]}% 的净利率上屏'
    hit = [c for c in d['caveats'] if c['code'] == 'source_ratio_absurd']
    assert hit, '挡了格子却不报名字，等于静默藏数'
    assert 'null' in hit[0]['text'], hit[0]['text']


def test_可读比率这个口子自己不漏():
    assert F.readable_ratio(999.9, F.RATIO_ABS_MAX_PERCENT) == pytest.approx(999.9)
    assert F.readable_ratio(-999.9, F.RATIO_ABS_MAX_PERCENT) == pytest.approx(-999.9)
    assert F.readable_ratio(1000.1, F.RATIO_ABS_MAX_PERCENT) is None
    assert F.readable_ratio(None, F.RATIO_ABS_MAX_PERCENT) is None
    assert F.readable_ratio(float('nan'), F.RATIO_ABS_MAX_PERCENT) is None
    assert F.readable_ratio(float('inf'), F.RATIO_ABS_MAX_PERCENT) is None
    assert F.readable_ratio('12.5', F.RATIO_ABS_MAX_PERCENT) == pytest.approx(12.5)
    assert F.readable_ratio('不是数', F.RATIO_ABS_MAX_PERCENT) is None
    assert F.readable_ratio(9999.0, F.YOY_SRC_ABS_MAX_PERCENT) == pytest.approx(9999.0)
    assert F.readable_ratio(10001.0, F.YOY_SRC_ABS_MAX_PERCENT) is None


# ------------------------------------------------------------------ 读侧的诚实性

def test_读侧把元换成亿元并且缺格是_none():
    d = SF._moneyify({'value': 1_000_000_000.0, 'method_a': {'value': 2e8},
                      'missing_quarters': [{'period': '2026-12-31', 'value': 3e8}]}, ('value',))
    assert d['value'] == pytest.approx(10.0)
    assert d['method_a']['value'] == pytest.approx(2.0)
    assert d['missing_quarters'][0]['value'] == pytest.approx(3.0)
    assert SF._yi(None) is None


def test_没交代码时给available是False不抛():
    d = SF.board('')
    assert d['available'] is False and d['reason']


# ------------------------------------------------------------------ 两条不打桩：现读真库

def test_真库这份财务能被_json_parse_且金额是亿元量级():
    from app.db import engine
    r = engine.query_one(
        'select security_code, count(*) n, count(*) filter (where period_kind=%s) q '
        f'from {SF.TABLE} group by 1 having count(*) > 40 order by q desc limit 1',
        ('single',), role='reader')
    assert r, '财务多期表一场都没采进 —— 采集器没跑还是入库坏了？'
    code = r['security_code']
    d = SF.board(code)
    assert d['available'] is True, d.get('reason')
    assert d['quarters'] and d['annual']
    for row in d['quarters']:
        assert len(str(row['period'])) == 10, '报告期没被清洗成 ISO 串'
        if row.get('revenue') is not None:
            assert 0.01 < abs(row['revenue']) < 20_000, f'{row["period"]} 还是元？{row["revenue"]}'
    s = json.dumps({'data': d}, ensure_ascii=False, allow_nan=False)
    assert 'NaN' not in s and 'NaT' not in s and 'Infinity' not in s
    assert '**' not in s, '服务到屏幕的散文里还有 markdown 粗体标记'
    assert d['caveats'] and all(c['code'] and c['text'] for c in d['caveats'])
    assert d['definitions'], '字段义没交出去，页面就只能自己编'


def test_没采过的代码老实说没采过():
    d = SF.board('688999.SH')          # 主档里不存在的号
    assert d['available'] is False
    assert 'rows' not in d and 'quarters' not in d, '不可用还带着行键，页面会读成"有但为空"'
    assert d['reason']
