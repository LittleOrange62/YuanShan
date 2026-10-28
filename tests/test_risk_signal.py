# -*- coding: utf-8 -*-
"""「风险提示」读侧（`duanxian/risk_signal.py`）的单测：纯判据 + 读侧诚实性。

全部不碰真库：`pg.enabled` / `pg.fetch_df` 用假路由替掉，
每条断言对应一种"屏幕会说谎"的情形：
 · 缺数/没回填被摆成 0 或"无明显背离"；
 · 样本不足被硬凑（用现有几期算分位、补 50）；
 · 周/月把日比值取平均冒充周期值；
 · 非法参数被静默当成没筛；
 · 响应里漂着 NaN/Infinity（浏览器 JSON.parse 直接炸）。
"""
from __future__ import annotations

import datetime as dt
import json
import math

import pandas as pd
import pytest

from duanxian import risk_signal as R

COLS_BASE = ['trade_date', 'stocks', 'up_count', 'down_count', 'up_ratio', 'limit_up_count']
COLS_ARMS = COLS_BASE + ['up_amount', 'down_amount', 'arms_ratio']


def _df(records, columns):
    if not records:
        return pd.DataFrame([], columns=columns)
    return pd.DataFrame([{c: r.get(c) for c in columns} for r in records])


def _day(i: int) -> dt.date:
    return dt.date(2026, 6, 1) + dt.timedelta(days=i)


def _rows(n=25, arms=True, up_ratio_holes=()):
    out = []
    for i in range(n):
        d = _day(i)
        r = {'trade_date': d.isoformat(), 'stocks': 5000,
             'up_count': 2000 + i * 10, 'down_count': 2500 - i * 5,
             'limit_up_count': 40 + i,
             'up_ratio': (50.0 + i * 0.5) if i not in up_ratio_holes else None}
        if arms:
            r['up_amount'] = 1e11 * (1 + i)
            r['down_amount'] = 1e11
            r['arms_ratio'] = 1.0 + i
        out.append(r)
    return out


class FakePg:
    """按 SQL 里的指纹串路由的假 fetch_df；列在/不在、回填/没回填都由 flags 控制。"""

    def __init__(self, *, cols=True, global_arms=0, rows=None, coverage=None, fetch_fail=False):
        self.cols = cols
        self.global_arms = global_arms          # count(arms_ratio) 的全库非空行数
        self.rows = rows if rows is not None else _rows()
        self.coverage = coverage if coverage is not None else {
            'n': max(1, len(self.rows)), 'lo': '2021-08-02',
            'hi': self.rows[-1]['trade_date'] if self.rows else '2026-10-20'}
        self.fetch_fail = fetch_fail

    def __call__(self, sql, params=()):
        if self.fetch_fail:
            return None
        if 'information_schema.columns' in sql:
            return _df([{'column_name': c} for c in (COLS_ARMS if self.cols else COLS_BASE)],
                       ['column_name'])
        if 'count(arms_ratio)' in sql:
            g = self.global_arms
            return _df([{'n_arms': g, 'n_up_amt': g, 'n_down_amt': g,
                         'last_arms': '2026-08-25' if g else None}],
                       ['n_arms', 'n_up_amt', 'n_down_amt', 'last_arms'])
        if 'count(*)' in sql:
            return _df([self.coverage], ['n', 'lo', 'hi'])
        if 'from analytics.market_breadth' in sql:
            return _df(self.rows, COLS_ARMS if self.cols else COLS_BASE)
        raise AssertionError(f'假库没认出的 SQL：{sql[:60]}')


@pytest.fixture
def fake(monkeypatch):
    def _install(**flags):
        monkeypatch.setattr(R.pg, 'enabled', lambda: True)
        monkeypatch.setattr(R.pg, 'fetch_df', FakePg(**flags))
    return _install


# ---------------------------------------------------------------- 纯判据

class TestPercentile:
    def test_样本不满20期_一律None不硬凑(self):
        vals = [float(i) for i in range(19)]
        assert R.percentile_of(vals, 18.0) is None          # 19 个就是判不了，不拿 19 算
        assert R.percentile_of([], None) is None

    def test_满20期_百分位是占比含自身(self):
        vals = [float(i) for i in range(20)]
        assert R.percentile_of(vals, 0.0) == 5.0             # 最小：只有它自己 ≤ 它
        assert R.percentile_of(vals, 19.0) == 100.0
        assert R.percentile_of(vals, 9.0) == 50.0

    def test_滚动窗口只看近20期(self):
        # 20 个旧高点滑出窗口后不该再压低末值的分位：全局算会是 50，只看近 20 期才是 5
        vals = [1.0] * 20 + [2.0] * 19 + [1.0]
        pct = R.trailing_pctiles(vals)
        assert pct[:19] == [None] * 19
        assert pct[19] == 100.0                               # 前 20 个全等：≤ 它的样本=20
        assert pct[-1] == 5.0                                 # 末值窗口里只有它自己 ≤ 它
        assert R.trailing_pctiles([float(i) for i in range(40)])[39] is not None

    def test_有空洞的窗口_相关期全是None不补50(self):
        vals = [float(i) for i in range(20)]
        vals[5] = None
        pct = R.trailing_pctiles(vals)
        assert all(x is None for x in pct[5:20])              # 洞在 20 期窗口里的期都判不了
        assert pct[19] is None
        assert R.trailing_pctiles([float(i) for i in range(40)])[39] is not None


class TestBands:
    def test_档位边界(self):
        assert R.divergence_band(40.0) == ('high', R.LEVEL_LABELS['high'])
        assert R.divergence_band(39.9) == ('moderate', R.LEVEL_LABELS['moderate'])
        assert R.divergence_band(20.0) == ('moderate', R.LEVEL_LABELS['moderate'])
        assert R.divergence_band(19.9) == ('none', R.LEVEL_LABELS['none'])
        assert R.divergence_band(-40.0) == ('reverse', R.LEVEL_LABELS['reverse'])
        assert R.divergence_band(-39.9) == ('none', R.LEVEL_LABELS['none'])

    def test_判不了不是无明显背离(self):
        assert R.divergence_band(None) == (None, None)
        assert R.divergence_band(float('nan')) == (None, None)


class TestCalendarMath:
    def test_月末钳制(self):
        assert R.month_start(dt.date(2026, 3, 31), 3) == dt.date(2025, 12, 31)
        assert R.month_start(dt.date(2028, 3, 31), 1) == dt.date(2028, 2, 29)   # 闰年
        assert R.month_start(dt.date(2026, 3, 31), 1) == dt.date(2026, 2, 28)

    def test_周桶用ISO周(self):
        p, label = R.bucket_of(dt.date(2026, 10, 20), 'week')   # 周二
        assert p == '2026-W43' and label == p
        assert R.bucket_of(dt.date(2026, 10, 20), 'month')[0] == '2026-10'


class TestAggregate:
    def test_周值是合计重算_不是日比值取平均(self):
        members = [
            {'trade_date': '2026-08-03', 'stocks': 5000, 'up_count': 4000, 'down_count': 500,
             'up_ratio': 80.0, 'up_amount': 9e10, 'down_amount': 1e10, 'arms_ratio': 9.0},
            {'trade_date': '2026-08-04', 'stocks': 5000, 'up_count': 1000, 'down_count': 3500,
             'up_ratio': 20.0, 'up_amount': 1e10, 'down_amount': 9e10, 'arms_ratio': round(1 / 9, 6)},
        ]
        week = R.aggregate_periods(members, 'week')[0]
        assert week['bars_in_period'] == 2
        assert week['arms_ratio'] == round(1e11 / 1e11, 6)     # Σ90+10 ÷ Σ10+90 = 1.0
        assert week['arms_ratio'] != round((9.0 + 1 / 9) / 2, 6)   # 取平均的那个假数
        assert week['up_ratio'] == 50.0                       # Σ5000 ÷ Σ10000
        assert week['up_count'] == 2500.0                     # 日均
        assert week['down_count'] == 2000.0
        assert week['net'] == 500.0                           # 日均涨 − 日均跌

    def test_成员日缺分量_整期不硬凑(self):
        members = [
            {'trade_date': '2026-08-03', 'stocks': 5000, 'up_count': 4000, 'down_count': 500,
             'up_ratio': 80.0, 'up_amount': 9e10, 'down_amount': 1e10, 'arms_ratio': 9.0},
            {'trade_date': '2026-08-04', 'stocks': 5000, 'up_count': 1000, 'down_count': 3500,
             'up_ratio': 20.0, 'up_amount': None, 'down_amount': 9e10, 'arms_ratio': None},
        ]
        week = R.aggregate_periods(members, 'week')[0]
        assert week['arms_ratio'] is None and week['up_amount'] is None
        assert week['up_count'] == 2500.0                     # 家数照旧按有的成员取日均
        assert week['bars_in_period'] == 2                    # 成员天数照交，缺数自己会说

    def test_下跌侧合计为0_比值判不出来(self):
        members = [{'trade_date': '2026-08-03', 'stocks': 5000, 'up_count': 4000, 'down_count': 0,
                    'up_ratio': 80.0, 'up_amount': 9e10, 'down_amount': 0.0, 'arms_ratio': None}]
        assert R.aggregate_periods(members, 'day')[0]['arms_ratio'] is None


# ---------------------------------------------------------------- 读侧诚实性

class TestPanelStates:
    def test_库不通_没有series键(self, monkeypatch):
        monkeypatch.setattr(R.pg, 'enabled', lambda: False)
        p = R.panel()
        assert p['available'] is False and 'series' not in p
        assert '连不上' in p['reason']

    def test_查询失败_是库不通不是没数据(self, monkeypatch):
        monkeypatch.setattr(R.pg, 'enabled', lambda: True)
        monkeypatch.setattr(R.pg, 'fetch_df', lambda sql, params=(): None)
        p = R.panel()
        assert p['available'] is False and 'series' not in p
        assert '库读不到' in p['reason']

    def test_表里没有一行_给出加工链命令(self, fake):
        fake(coverage={'n': 0, 'lo': None, 'hi': None}, rows=[])
        p = R.panel()
        assert p['available'] is False and 'series' not in p
        assert '还没有任何一行' in p['reason'] and 'app.compute.breadth --backfill' in p['reason']

    def test_窗口里没有行_说清最近一场(self, fake):
        fake(rows=[], coverage={'n': 900, 'lo': '2021-08-02', 'hi': '2026-10-20'})
        p = R.panel(date='2020-01-02')
        assert p['available'] is False and 'series' not in p
        assert '没有 breadth 行' in p['reason'] and '2026-10-20' in p['reason']

    def test_列还没建_arms不带series但宽度照出(self, fake):
        fake(cols=False)
        p = R.panel()
        assert p['available'] is True and len(p['series']) == 25
        assert 'series' not in p['arms']
        assert p['arms']['available'] is False
        assert 'app.compute.breadth --backfill' in p['arms']['reason']
        assert all(r['arms_ratio'] is None and r['divergence'] is None for r in p['series'])
        assert any(c['code'] == 'arms_missing' for c in p['caveats'])

    def test_列在但全库没回填_与列缺失措辞不同(self, fake):
        fake(cols=True, global_arms=0,
             rows=[dict(r, up_amount=None, down_amount=None, arms_ratio=None) for r in _rows()])
        p = R.panel()
        assert p['arms']['available'] is False and 'series' not in p['arms']
        assert '还没有回填过一行' in p['arms']['reason'] and '没有这种列' not in p['arms']['reason']
        assert 'app.compute.breadth --backfill' in p['arms']['reason']

    def test_回填没铺到本窗口_第三种说法(self, fake):
        rows = [dict(r, up_amount=None, down_amount=None, arms_ratio=None) for r in _rows()]
        fake(cols=True, global_arms=500, rows=rows,
             coverage={'n': 1200, 'lo': '2021-08-02', 'hi': rows[-1]['trade_date']})
        p = R.panel()
        assert p['arms']['available'] is False and 'series' not in p['arms']
        assert '本窗口' in p['arms']['reason'] and '500' in p['arms']['reason']

    def test_回填好了_arms带series且背离能判(self, fake):
        fake(cols=True, global_arms=900)
        p = R.panel()
        assert p['arms']['available'] is True
        assert len(p['arms']['series']) == len(p['series']) == 25
        # 前 19 期样本不足：null；后 6 期都算得出来
        assert all(r['width_pctile'] is None and r['divergence'] is None for r in p['series'][:19])
        last = p['series'][-1]
        assert last['width_pctile'] == 100.0 and last['arms_pctile'] == 100.0
        assert last['divergence'] == 0.0 and last['level'] == 'none'
        assert p['latest']['divergence'] == 0.0
        assert ({k: v for k, v in p['thresholds'].items() if k != 'extremes'}
                == {'window': 20, 'high': 40.0, 'moderate': 20.0, 'reverse': -40.0})
        # 10-21 起 thresholds 多带一份 extremes：判据加了一处就要一起钉，页面才有唯一出处
        assert p['thresholds']['extremes'] == R.EXTREME_THRESHOLDS
        assert p['levels']['high'] == '高危：家数在走强、钱没跟上'
        assert '相对量、不是概率' in p['rule']

    def test_窗口只有14期_分位一律null不硬凑(self, fake):
        fake(rows=_rows(n=12))
        p = R.panel(months=3)
        assert len(p['series']) == 12
        assert all(r['divergence'] is None and r['level'] is None for r in p['series'])
        assert p['latest'] is None
        codes = [c['code'] for c in p['caveats']]
        assert 'pct_window' in codes and 'pct_warmup' not in codes

    def test_非法参数_出声退回并在caveats点名收到的值(self, fake):
        fake()
        p = R.panel(months=5, group='hour', date='08-04')
        assert p['months'] == 3 and p['group'] == 'day'
        texts = '；'.join(c['text'] for c in p['caveats'])
        assert 'months=5' in texts and '退回 3' in texts
        assert "group='hour'" in texts and '退回按日' in texts
        assert "date='08-04'" in texts

    def test_合法参数_照单全收(self, fake):
        fake()
        for m in (3, 6, 12, 24):
            assert R.panel(months=m)['months'] == m
        for g in ('day', 'week', 'month'):
            assert R.panel(group=g)['group'] == g

    def test_按周聚合_caveats点名库里没存周期行(self, fake):
        fake(rows=_rows(n=30))
        p = R.panel(group='week')
        assert all(r['bars_in_period'] >= 1 for r in p['series'])
        pr = next(c for c in p['caveats'] if c['code'] == 'period_recompute')
        assert '库里没存周期行' in pr['text'] and '合计' in pr['text']
        assert 'Σup_amount' in p['definitions']['week_month'].replace(' ', '')

    def test_响应是合法JSON_没有NaNInfinity(self, fake):
        fake()
        blob = json.dumps(R.panel(group='week'), ensure_ascii=False)
        assert 'NaN' not in blob and 'Infinity' not in blob
        json.loads(blob)

    def test_up_ratio有空洞_点名行数而不是悄悄跳过(self, fake):
        fake(rows=_rows(up_ratio_holes=(7,)))
        p = R.panel()
        hole = next(c for c in p['caveats'] if c['code'] == 'up_ratio_holes')
        assert '1 行' in hole['text']
        assert p['series'][7]['width_pctile'] is None


class TestNumCleaning:
    def test_NaN与NaT都变None_日期原样(self):
        from decimal import Decimal
        assert R._num(float('nan')) is None
        assert R._num(pd.Timestamp('NaT')) is None
        assert R._num(pd.NA) is None
        assert math.isfinite(R._num(Decimal('3.5')))          # Decimal 过闸成 float
        assert R._num(dt.date(2026, 10, 20)) == dt.date(2026, 10, 20)


def _all_strings(o):
    """把会念到屏幕上的每一根串收出来（dict 的值、list 的元素、字符串本身）。"""
    if isinstance(o, dict):
        for v in o.values():
            yield from _all_strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _all_strings(v)
    elif isinstance(o, str):
        yield o


class TestScreenTextHygiene:
    """上屏措辞的闸：不许 markdown 星号，不许 ASCII 双引号（项目口径一律用「」）。

    判据全绿而屏幕上念出 `**分位是相对量、不是概率**` 是 2026-10-21 真踩到的：首页那一块
    走纯文本渲染，星号没有任何一层会剥掉，用户读到的就是一对星号。所以这里不看判据，
    只看"念出来的样子"，并把每一种状态（列没建 / 全库没回填 / 回填没铺到本窗口 / 非法参数 /
    按周按月 / 库不通）都过一遍——坏话往往只写在兜底那几句里。
    """

    def test_常量层干净(self):
        for s in _all_strings({'rule': R.RULE, 'definitions': R.DEFINITIONS,
                               'levels': R.LEVEL_LABELS}):
            assert '**' not in s, s[:80]
            assert '"' not in s, s[:80]

    def test_每一种状态的上屏文字都干净(self, fake):
        fake(cols=True, global_arms=900)
        panels = [('按日', R.panel()), ('按周', R.panel(group='week')),
                  ('按月', R.panel(group='month')), ('24 个月', R.panel(months=24)),
                  ('非法参数', R.panel(months=5, group='hour', date='08-04'))]
        fake(cols=False)
        panels.append(('列没建', R.panel()))
        fake(cols=True, global_arms=0)
        panels.append(('全库没回填', R.panel()))
        fake(cols=True, global_arms=900, rows=_rows(n=25, arms=False))
        panels.append(('回填没铺到本窗口', R.panel()))
        fake(fetch_fail=True)
        panels.append(('库不通', R.panel()))
        bad = []
        for name, p in panels:
            for s in _all_strings(p):
                if '**' in s or '"' in s:
                    bad.append(f'{name}: {s[:70]}')
        assert not bad, '\n'.join(bad)


# ------------------------------------------------------ 10-21 追加：当日「家数 vs 钱」那一把尺与极端点位
# 用户 2026-10-21 那段分析的形状：9-21 宽度 80%、涨停 104 家，看着很强，但上涨票只吃掉
# 七成出头的成交 ⇒ 那 18% 在跌的票占了近三成成交额；第二天宽度直接掉下去。
# 旧的「宽度分位 − 阿姆斯分位」抓不到它（那天阿姆斯本身也在自己的历史高位，分位差只有 15），
# 所以这里补的是**同一天内部**的那把尺 —— 三把尺并存，谁也不顶替谁。

def _daily(**over):
    """造一行日线口径的 breadth 原值（字段与 `analytics.market_breadth` 那几列一致）。"""
    r = {'trade_date': '2026-09-21', 'stocks': 5400, 'up_count': 4405, 'down_count': 912,
         'limit_up_count': 102, 'up_ratio': 81.62,
         'up_amount': 1.42e12, 'down_amount': 5.65e11, 'arms_ratio': 2.5127}
    r.update(over)
    return r


def _view(rows):
    return R.attach_ratio_view(R.aggregate_periods(rows, 'day'))[0]


class TestSameDayRuler:
    def test_家数比与成交额占比都按分量算(self):
        p = _view([_daily()])
        assert p['count_ratio'] == round(4405 / 912, 3)
        assert p['amount_share_up'] == round(100.0 * 1.42e12 / (1.42e12 + 5.65e11), 2)
        # 81.62 − 71.51 ≈ +10.1pp：家数比钱多，就是用户说的那句"量价背离"
        assert p['share_gap'] == round(p['up_ratio'] - p['amount_share_up'], 2)
        assert p['share_gap'] > 8

    def test_下跌侧家数为0_家数比是None不是无限大(self):
        p = _view([_daily(down_count=0, down_amount=0.0, arms_ratio=None)])
        assert p['count_ratio'] is None, '除以 0 不写成 inf：那一天"倍数"没有意义'
        assert p['amount_share_up'] == 100.0, '跌侧一分钱没有 ⇒ 涨侧占满，这是真读数'

    def test_分量没落库时从比值反推_但缺数就是None(self):
        p = _view([_daily(up_amount=None, down_amount=None, arms_ratio=3.0)])
        assert p['amount_share_up'] == 75.0            # 3/(1+3)
        q = _view([_daily(up_amount=None, down_amount=None, arms_ratio=None)])
        assert q['amount_share_up'] is None and q['share_gap'] is None, '两样都没有就是判不了，不补 50'
        # 比值是 0（涨侧一分钱没有）是实质读数，不许被 `or` 兜成 None
        z = _view([_daily(up_amount=None, down_amount=1e11, arms_ratio=0.0)])
        assert z['amount_share_up'] == 0.0

    def test_周档也带这把尺_涨停交的是期内日均(self):
        wk = R.aggregate_periods([_daily(trade_date='2026-09-21'),
                                  _daily(trade_date='2026-09-22', up_count=2000, down_count=3000,
                                         limit_up_count=60, up_ratio=40.0)], 'week')
        p = R.attach_ratio_view(wk)[0]
        assert p['limit_up_count'] == 81.0, '(102+60)/2 —— 周/月那两档的涨停家数是日均，不是周内合计'
        assert p['limit_up_days'] == 2
        assert p['share_gap'] is not None


class TestExtremes:
    def test_背离档_宽度够高且家数比钱多才命中(self):
        th = dict(R.EXTREME_THRESHOLDS)
        rows = [_daily(),                                             # 81.62% / +10.1pp ⇒ 命中
                _daily(trade_date='2026-09-22', up_ratio=42.62),      # 宽度不够（42.62 < 80）
                _daily(trade_date='2026-09-23', up_ratio=90.0, up_amount=8.0e11,
                       down_amount=1.0e11, arms_ratio=8.0),            # 钱跟着家数走（share 88.9 ⇒ gap +1.1）
                _daily(trade_date='2026-09-24', up_ratio=79.0, up_amount=3.0e11,
                       down_amount=4.0e11, arms_ratio=0.75)]           # gap 很大但宽度差 1pp
        e = R.find_extremes(R.attach_ratio_view(R.aggregate_periods(rows, 'day')))
        assert [x['period'] for x in e['divergence']['rows']] == ['2026-09-21']
        assert e['divergence']['matched'] == 1
        assert e['thresholds'] == th, '阈值原样交回，界面读它'
        assert e['kind_labels']['divergence'] == R.EXTREME_KIND_LABELS['divergence']

    def test_崩塌档点名上一期与掉多少(self):
        rows = [_daily(),
                _daily(trade_date='2026-09-22', up_ratio=42.62, up_count=2300, down_count=3088)]
        e = R.find_extremes(R.attach_ratio_view(R.aggregate_periods(rows, 'day')))
        got = e['collapse']['rows']
        assert len(got) == 1 and got[0]['period'] == '2026-09-22'
        assert got[0]['prev_period'] == '2026-09-21', '崩点要说清「松手之前」是哪一天'
        assert got[0]['drop_pp'] == round(81.62 - 42.62, 2)

    def test_每类各取前N_但最近20期内的命中一定留(self):
        """老点按严重度取前 `max_marks`，最近的命中一定在列 —— 这块屏是天天读的。

        构造 30 期：前 20 期全是背离点且 gap 一期比一期小（30pp → 11pp，全过 8pp 门槛），
        后 10 期宽度只有 50% 不命中。于是"最严重的 6 个"= 头 6 期，"最近 20 期"= 第 11~30 期
        ⇒ 第 11~20 期这 10 个背离点即使排在第 7 名之后也必须留，被挤掉的只有第 7~10 期那 4 个。
        """
        rows = []
        for i in range(30):
            if i < 20:
                share = 60.0 + i                     # gap = 90 − share = 30 − i（递减且都 ≥8）
                rows.append(_daily(trade_date=_day(i).isoformat(), up_ratio=90.0,
                                   up_amount=share * 1e10, down_amount=(100 - share) * 1e10,
                                   arms_ratio=share / (100 - share)))
            else:
                rows.append(_daily(trade_date=_day(i).isoformat(), up_ratio=50.0))
        e = R.find_extremes(R.attach_ratio_view(R.aggregate_periods(rows, 'day')))
        d = e['divergence']
        kept = {x['period'] for x in d['rows']}
        assert d['matched'] == 20
        assert len(d['rows']) == 6 + 10, '前 6 严重的 + 最近 20 期里那 10 个（不重复计）'
        assert d['dropped_by_cap'] == 20 - len(d['rows']) == 4
        assert { _day(i).isoformat() for i in range(6) } <= kept
        assert { _day(i).isoformat() for i in range(10, 20) } <= kept
        assert _day(8).isoformat() not in kept, '第 9 名又不在最近 20 期 ⇒ 才是真被挤掉的那个'
        assert e['periods_in_window'] == 30

    def test_算不出成交额占比的期另计_不等于没背离(self):
        rows = [_daily(up_amount=None, down_amount=None, arms_ratio=None) for _ in range(3)]
        e = R.find_extremes(R.attach_ratio_view(R.aggregate_periods(rows, 'day')))
        assert e['divergence']['matched'] == 0
        assert e['skipped_no_gap'] == 3, '判不了的期数必须交回去，不然图上没点＝"这段很安全"是句假话'

    def test_面板把新东西都交齐并带自救那句(self, fake):
        rows = [_daily(), _daily(trade_date='2026-09-22', up_ratio=42.62)] + _rows(25)
        rows[0]['trade_date'] = rows[2]['trade_date']      # 别让手工那两天撞上序列日期
        fake(rows=rows)
        p = R.panel(months=3, group='day')
        assert p['available']
        assert 'extremes' in p and 'divergence' in p['extremes'] and 'collapse' in p['extremes']
        assert p['thresholds']['extremes'] == R.EXTREME_THRESHOLDS
        assert 36 in p['month_options'] and 60 in p['month_options'], \
            '24 个月窗口按"今天往回 24 个月"截，24 年 10 月那段在窗外 —— 要靠这两档看得到'
        assert 'day' in p['group_options']
        for k in ('share_gap', 'count_ratio', 'amount_share_up', 'extremes'):
            assert k in p['definitions'], f'{k} 没定义就上路，屏幕会用页面上的话猜判据'
        assert all('share_gap' in r and 'limit_up_count' in r for r in p['series'])


class TestTrendLayer:
    """趋势与转折点（用户 2026-10-26：「这个图看不出趋势和转折点」）。

    判据是：转折点打在平滑后的**水位线**上，背离那一族只交密度线。
    每条断言对应一种「把噪声画成信号」的情形。
    """

    def test_均线要满窗口_否则None不拿几期硬凑(self):
        assert R.moving_average([1.0, 2.0, 3.0, 4.0], 3) == [None, None, 2.0, 3.0]
        assert R.moving_average([], 3) == []
        # 洞不计入样本数：满 k 个**有效值**才出数（不然头几期会被算成偏低的假水位）
        assert R.moving_average([1.0, None, 3.0, 4.0], 3) == [None, None, None, None]
        assert R.moving_average([1.0, 2.0, 4.0], 3) == [None, None, round(7 / 3, 2)]

    def test_zigzag_涨到顶再跌_认出那个顶(self):
        vals = list(range(0, 101, 10)) + [80, 60, 40, 20, 0]
        got = R.zigzag_turns([float(v) for v in vals], 10.0)
        assert [(t['i'], t['value'], t['kind']) for t in got] == [(10, 100.0, 'peak')]

    def test_zigzag_先跌再涨也认得出谷(self):
        vals = list(range(100, -1, -10)) + [20, 40, 60, 80, 100]
        got = R.zigzag_turns([float(v) for v in vals], 10.0)
        assert [(t['i'], t['value'], t['kind']) for t in got] == [(10, 0.0, 'trough')]

    def test_zigzag_高点在前低点在后也认得出(self):
        """⚠️ 回归钉：第一版在方向未定时 hi/lo 共用一个极值变量 ⇒ 极值被后到的点覆盖，
        12 个月 MA10 上 th=5pp 报 34 个、th=10pp 报 0 个（极差 31pp 却一个不报）。"""
        got = R.zigzag_turns([50.0, 100.0, 95.0, 90.0, 85.0, 80.0, 70.0, 60.0, 50.0, 40.0], 10.0)
        assert [(t['i'], t['value'], t['kind']) for t in got] == [(1, 100.0, 'peak')]
        got2 = R.zigzag_turns([50.0, 0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 60.0], 10.0)
        assert [(t['i'], t['value'], t['kind']) for t in got2] == [(1, 0.0, 'trough')]

    def test_zigzag_幅度不够就不算转折(self):
        assert R.zigzag_turns([50.0, 60.0, 52.0, 63.0, 55.0], 10.0) == [], \
            '60→52 只有 8pp、63→55 只有 8pp：都不够门槛，不许标成转折点'

    def test_zigzag_末端未确认的极值不交(self):
        """最后一跌还没被反向走够门槛 ⇒ 那个谷是"猜的"，交出去就是事后诸葛。"""
        got = R.zigzag_turns([float(v) for v in range(0, 101, 10)], 10.0)
        assert got == [], '一路上涨里没有已确认的反转'

    def test_zigzag_窗口第一期不当转折点_洞不影响索引(self):
        vals = [0.0, None, 50.0, 100.0, None, 80.0, 60.0]
        got = R.zigzag_turns(vals, 10.0)
        assert [(t['i'], t['value'], t['kind']) for t in got] == [(3, 100.0, 'peak')], \
            'i 必须是**原序列**的下标（洞跳过后仍要对得上图上那一格）'
        # 第一期本身就算极值也不交（它没有前置形态），改由 find_turns 的 boundary 说出来
        assert R.zigzag_turns([100.0, 80.0, 60.0, 40.0, 20.0], 10.0) == []

    def test_zigzag_门槛非法或样本太少一律空(self):
        vals = [float(v) for v in range(0, 101, 10)]
        for th in (0.0, -5.0, None, float('nan')):
            assert R.zigzag_turns(vals, th) == [], f'门槛 {th} 不合法 ⇒ 不画，不是"全部画"'
        assert R.zigzag_turns([1.0, 2.0], 10.0) == []

    def test_密度要满滚动样本_且等于门槛也算命中(self):
        assert R.hit_density([50.0, 50.0, 10.0, 20.0, 30.0], 20.0, 3) == [None, None, 2, 2, 2]
        assert R.hit_density([None, 20.0, 30.0], 20.0, 3) == [None, None, None], \
            '样本不满就是判不了（None），不许落成 0 —— 0 会被读成「这段一点都不危险」'

    def test_单调上升那一段_没有转折点但窗口第一期是谷(self):
        rows = [_daily(trade_date=_day(i).isoformat(), up_ratio=40.0 + i) for i in range(26)]
        series = R.attach_trend(R.attach_divergence(R.aggregate_periods(rows, 'day')))
        t = R.find_turns(series)
        assert t['smooth'] == R.TREND_SMOOTH and t['zigzag_pp'] == R.TREND_ZIGZAG_PP, '参数只住后端、原样交出'
        assert t['rows'] == []
        assert t['boundary'] and t['boundary']['kind'] == 'trough', \
            '一路上涨 ⇒ 窗口第一期就是最低点，这件事要由 boundary 说出来，不能悄悄丢掉'
        assert t['boundary']['period'] == _day(R.TREND_SMOOTH - 1).isoformat()
        assert [p['up_ratio_ma'] for p in series[:R.TREND_SMOOTH - 1]] == [None] * (R.TREND_SMOOTH - 1)
        assert series[-1]['up_ratio_ma'] == round(sum(40.0 + i for i in range(16, 26)) / 10, 2) == 60.5

    def test_面板把趋势那三样都交齐_判不了的两样各出一条caveat(self, fake):
        rows = [_daily(trade_date=_day(i).isoformat(), up_ratio=40.0 + i) for i in range(30)]
        fake(rows=rows)
        p = R.panel(months=3, group='day')
        assert p['available']
        t = p['trend']
        assert (t['smooth'], t['zigzag_pp']) == (R.TREND_SMOOTH, R.TREND_ZIGZAG_PP)
        dn = t['density']
        assert (dn['window'], dn['threshold'], dn['periods']) == (R.DENSITY_WINDOW, R.DIV_MID, 30)
        assert dn['judged'] == 11, '分位要满 20 期 ⇒ 30 期里只有第 20~30 期算得出背离'
        assert dn['computable'] == 0, '背离刚够出分位，密度还要再满 20 期 ⇒ 这一档整条 null'
        cav = {c.get('code'): (c.get('text') or '') for c in p['caveats']}
        assert 'density_unjudged' in cav and '判不了' in cav['density_unjudged']
        assert 'turns_none' in cav and '未确认' in cav['turns_none']
        for k in ('trend', 'trend_basis', 'risk_density', 'divergence_scale'):
            assert k in p['definitions'], f'{k} 没定义就上路，屏幕会自己编一句判据'
        assert all('up_ratio_ma' in r and 'risk_density' in r for r in p['series'])
        assert 'up_ratio_ma' in (p['latest'] or {}) and 'risk_density' in (p['latest'] or {})

    def test_latest的趋势两列取自同一期_不许拿另一期的值冒充当前读数(self, fake):
        """`latest` 是**最后一个算得出背离的期**；趋势两列必须跟着它一起走。

        构造：最后一期宽度缺数 ⇒ 它连分位都算不出，latest 落在倒数第二期。
        这一期的 MA 是真数、而最后一期的 MA 是 null —— 两边对不上就说明取错了期。
        """
        rows = [_daily(trade_date=_day(i).isoformat(), up_ratio=40.0 + i) for i in range(30)]
        rows[-1]['up_ratio'] = None          # 最后一期宽度缺数
        fake(rows=rows)
        p = R.panel(months=3, group='day')
        latest, last = p['latest'], p['series'][-1]
        assert latest['period'] == p['series'][-2]['period'] != last['period']
        assert last['up_ratio_ma'] is None, '缺数的末期连均线都出不来'
        assert latest['up_ratio_ma'] == p['series'][-2]['up_ratio_ma'] is not None
        assert latest['risk_density'] == p['series'][-2]['risk_density']


def test_真库_2026年9月21日的背离与次日崩塌都在清单里():
    """不打桩的一条：用户点名的那个日子必须在自动清单里，且不由页面挑。

    桩只证明判据按我想的跑；这条证明**库里真有这么一天、判据真认得它**。
    """
    if not R.pg.enabled():
        pytest.skip('本机 PG 没开')
    p = R.panel(months=3, group='day')
    if not p.get('available'):
        pytest.skip(f"库里读不到 breadth：{p.get('reason')}")
    e = p['extremes']
    assert '2026-09-21' in [x['period'] for x in e['divergence']['rows']], \
        f"9-21 不在背离清单里（命中 {e['divergence']['matched']} 期）"
    col = {x['period']: x for x in e['collapse']['rows']}
    hit = [v for v in col.values() if v['prev_period'] == '2026-09-21']
    assert hit, '9-21 → 9-22 那场宽度崩塌（−39pp）必须在崩点清单里'
    assert hit[0]['drop_pp'] > 25
    # 那一天库里真实读数：宽度 81.62%、102 家涨停、涨票吃掉 71.5% 的成交（用户报的 104 家是另一条口径）
    day = {r['period']: r for r in p['series']}['2026-09-21']
    assert 80 < day['up_ratio'] < 84 and 95 <= (day['limit_up_count'] or 0) <= 110
    assert 65 < day['amount_share_up'] < 78 and day['share_gap'] >= R.EXTREME_THRESHOLDS['share_gap_pp']
