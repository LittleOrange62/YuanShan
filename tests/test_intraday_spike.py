# -*- coding: utf-8 -*-
"""异动筛查（盘中事件）的判据与读侧诚实性。

判据那几例全是纯函数：`minute_metrics()` 吃一摞合成 bar，不碰库、不碰分钟表有多大。
读侧那几例钉的是"写错了不报错"的那类：参数与占位符数量、版本条件、
`%` 被 psycopg 当占位符吃掉、缺数被写成 0、以及**每个统计档必须与同期基线并排交**。
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from app.compute import intraday_spike as S


def bar(t, o, h, l, c, v=100.0, amt=None):
    return {'t': dt.datetime.combine(dt.date(2026, 10, 13), t), 'open': o, 'high': h,
            'low': l, 'close': c, 'volume_shares': v,
            'amount_yuan': amt if amt is not None else c * v}


def series(rows):
    """[(时刻, high, low, close, vol)] → bar 列表，open 一律取上一收。"""
    return [bar(t, prev_c, h, l, c, v) for (prev_c, (t, h, l, c, v))
            in zip([0.0] + [r[3] for r in rows[:-1]], rows)]


# ------------------------------------------------------------------ 分钟读数

def test_峰值时刻与站住分钟按合成序列算出():
    # 10:00 起到 10:02 拉到 +8%，之后一路回落到 +1%
    pre = 100.0
    seq = [(dt.time(9, 31), 100.5, 100.0, 100.0, 100),
           (dt.time(10, 0), 103.0, 100.0, 103.0, 200),
           (dt.time(10, 1), 106.0, 103.0, 106.0, 200),
           (dt.time(10, 2), 108.0, 106.0, 108.0, 200),
           (dt.time(10, 3), 108.0, 105.0, 105.0, 200),
           (dt.time(10, 4), 106.0, 102.0, 102.0, 200),
           (dt.time(10, 5), 102.0, 101.0, 101.0, 200)]
    bars = [bar(t, c, h, l, c, v) for (t, h, l, c, v) in seq]   # open 不参与判据
    m = S.minute_metrics(bars, pre, None)
    assert m['peak_time'] == dt.time(10, 2), m['peak_time']
    assert m['peak_min_no'] == 4                      # 09:31 是第 1 根
    assert m['above5_min'] == 4                       # high 站上 +5% 的那四根
    assert m['first5_time'] == dt.time(10, 1)
    # 15 根窗口里的最大拉升：窗口最低 100 → 最高 108
    assert m['speed15_pct'] == pytest.approx(8.0, abs=0.01)
    assert m['after5_dd_pct'] == pytest.approx(-6.48, abs=0.05)
    assert m['bars_used'] == 7


def test_峰值在第一根时前后量比留空而不是零():
    bars = [bar(dt.time(9, 31), 110, 110, 108, 108, 500),
            bar(dt.time(9, 32), 108, 108, 104, 104, 100)]
    m = S.minute_metrics(bars, 100.0, None)
    assert m['peak_time'] == dt.time(9, 31)
    assert m['pre_peak_min_vol'] is None
    assert m['fade_vol_ratio'] is None        # 写 0 会被读成"回落没人卖"，方向正好相反
    assert m['post_peak_min_vol'] == 100.0


def test_封住判据用分而不是半个百分点():
    # 10% 档：涨停价 11.00。收盘 10.95 距它 0.05 —— 用 0.5% 容差会被算成封住
    bars = [bar(dt.time(9, 31), 10.9, 11.0, 10.95, 10.95, 100)]
    m = S.minute_metrics(bars, 10.0, 11.0)
    assert m['touch_min'] == 1 and m['sealed_min'] == 0
    bars2 = [bar(dt.time(9, 31), 10.9, 11.0, 11.0, 11.0, 100)]
    m2 = S.minute_metrics(bars2, 10.0, 11.0)
    assert m2['sealed_min'] == 1


def test_开板段数数的是非封转封():
    bars = [bar(dt.time(9, 31), 11, 11, 11, 11, 10),      # 封
            bar(dt.time(9, 32), 11, 11, 10.8, 10.8, 20),   # 开
            bar(dt.time(9, 33), 11, 11, 11, 11, 10),       # 回封
            bar(dt.time(9, 34), 11, 11, 11, 11, 10)]
    m = S.minute_metrics(bars, 10.0, 11.0)
    assert m['sealed_min'] == 3 and m['board_runs'] == 2
    assert m['first_seal_time'] == dt.time(9, 31)


def test_均价线上下占比与尾盘占比():
    bars = [bar(dt.time(9, 31), 100, 101, 99, 101, 100, amt=101 * 100),
            bar(dt.time(14, 30), 101, 103, 101, 103, 100, amt=103 * 100),
            bar(dt.time(14, 31), 103, 104, 103, 104, 100, amt=104 * 100)]
    m = S.minute_metrics(bars, 100.0, None)
    # 第一根的收盘等于它自己的均价（严格"高于"才算在上方）⇒ 2/3
    assert m['above_vwap_pct'] == 66.7
    assert m['tail_above_vwap'] == 100.0


def test_空分钟序列不炸且不交假读数():
    m = S.minute_metrics([], 100.0, 110.0)
    assert m['bars_used'] == 0
    for k in ('peak_time', 'speed15_pct', 'above5_min', 'sealed_min', 'after5_dd_pct',
              'above_vwap_pct', 'fade_vol_ratio'):
        assert m[k] is None, k


# ------------------------------------------------------------------ 母体与标签

@pytest.mark.parametrize('hi,sp,touched,has_min,want', [
    (4.9, 9.0, False, True, False),        # 水平不够
    (6.0, 3.5, False, True, False),        # 慢了不算瞬间
    (6.0, 4.0, False, True, True),
    (6.0, 1.0, True, True, True),          # 摸到板就进（速度再慢也进）
    (6.0, None, False, False, True),       # 分钟线缺失 ⇒ 按水平收，但必须带 minute_available=false 出声
    (None, 8.0, False, True, False),
])
def test_母体四格与缺分钟线那一格(hi, sp, touched, has_min, want):
    assert S.qualifies(hi, sp, touched, has_min) is want


def test_标签只有一处生成且顺序固定():
    row = {'speed15_pct': 8.2, 'after5_dd_pct': -0.4, 'peak_time': dt.time(14, 41),
           'board_runs': 3, 'touched': True, 'sealed': True}
    assert S.spike_tags(row) == ['fast', 'touched', 'sealed', 'held5', 'late', 'reopen']
    assert set(S.TAG_LABELS) == {'fast', 'quick', 'slow', 'touched', 'sealed',
                                 'held5', 'late', 'reopen'}


def test_慢拉与守住与没守住互斥():
    assert S.spike_tags({'speed15_pct': 2.0, 'after5_dd_pct': -5.0}) == ['slow']
    assert S.spike_tags({'speed15_pct': None, 'after5_dd_pct': None}) == []


# ------------------------------------------------------------------ 写侧接线

def test_每条SQL的占位符与参数数量对齐(monkeypatch):
    """psycopg 不会替你数 `%s`；少一个绑一个就是 IndexError 或错位绑定。"""
    seen = []

    def fake_query(sql, params=None, *, role='reader'):
        seen.append((sql, params or ()))
        return []

    monkeypatch.setattr(S.engine, 'query', fake_query)
    monkeypatch.setattr(S.engine, 'query_one', lambda *a, **k: None)
    S.compute_day(dt.date(2026, 10, 13))
    assert seen, '一条 SQL 都没发'
    for sql, params in seen:
        assert sql.count('%s') == len(params), sql[:60]


def test_写库列与DDL列同名且带spec_version():
    assert 'spec_version' in S._COLUMNS and 'missing' in S._COLUMNS
    ddl = open('app/db/sql/32_intraday_spike.sql', encoding='utf-8').read()
    for c in S._COLUMNS:
        assert f'{c} ' in ddl or f'{c}  ' in ddl or f'{c}\\t' in ddl, f'{c} 不在 DDL 里'
    assert 'primary key (security_code, trade_date, spec_version)' in ddl
    assert S.UPSERT.count('{cols}') == 1 and 'on conflict' in S.UPSERT


def test_重算一场先清这一场这一版本的旧行(monkeypatch):
    """只 upsert 不清理 = **上一次半截计算的残留永远留在表里**。
    2026-10-15 实测：分钟线缺失那一场写了 224 条（水平那一刀判不了，整批
    `minute_available=false`），补跑之后真事件只有 218 条 —— 那 6 条不是"今天的读数判不了"，
    是昨天那一场半截计算留下的行；页面与 `count(*) filter (where not minute_available)`
    都会把它们当成今天的数。空批同样必须清场：那表示重算后这一场一条都不成立。"""
    sink: list = []

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            sink.append(('one', ' '.join(sql.split()), params))

        def executemany(self, sql, payload):
            sink.append(('many', ' '.join(sql.split()), len(payload)))

    monkeypatch.setattr(S.engine, 'cursor', lambda **kw: _Cur())
    day = dt.date(2026, 10, 15)
    row = {c: None for c in S._COLUMNS}
    row.update(security_code='600000.SH', trade_date=day, spec_version=S.SPEC_VERSION)

    assert S.write(day, [row]) == 1
    assert sink[0][0] == 'one' and sink[0][1].startswith('delete from analytics.intraday_spike'), \
        '写之前必须先删这一场这一版本（实测漏过这一步，表里多出 6 条假读数）'
    assert 'trade_date = %s' in sink[0][1] and 'spec_version = %s' in sink[0][1], \
        '删的条件要带 spec_version：换判据版本时不能把旧批一起洗掉（旧行留着才对账）'
    assert sink[0][2] == (day, S.SPEC_VERSION)
    assert sink[1][0] == 'many' and sink[1][2] == 1, '删完才 upsert，顺序不许反'

    sink.clear()
    assert S.write(day, []) == 0
    assert len(sink) == 1 and sink[0][1].startswith('delete'), '空批也要清场，否则旧的那一批永远留着'


def test_缺数写成None而不是0(monkeypatch):
    """0 会被下游读成"回落没人卖"/"没冲高"，方向正好相反。"""
    day = dt.date(2026, 10, 13)
    monkeypatch.setattr(S, '_daily_readings', lambda d: [{
        'security_code': '600000.SH', 'open': 10, 'high': 10.8, 'low': 10, 'close': 10.2,
        'pre_close': 10, 'limit_up': 11.0, 'limit_basis': 'x', 'pct_change': 2.0,
        'volume_shares': 1000, 'gain20': None, 'hi60': None, 'adjustment_factor': 1.0,
        'hi_pct': 8.0, 'cl_pct': 2.0, 'open_pct': 0.0, 'fade_pct': -5.56,
        'upper_shadow_pct': None, 'board_pct': 10.0, 'touched': False, 'sealed': False}])
    monkeypatch.setattr(S, '_minute_bars', lambda d, codes: {})
    rows = S.compute_day(day)
    assert len(rows) == 1, '分钟线缺失时该按水平档收进来并出声，不是丢掉'
    r = rows[0]
    assert r['minute_available'] is False
    assert r['speed15_pct'] is None and r['sealed_min'] is None and r['gain20_pct'] is None
    assert 'minute_bars' in (r['missing'] or '')
    assert r['spike_tags'] == []
    json.dumps({'rows': [dict(x, peak_time=None) for x in rows]}, default=str)


def test_refresh在分钟线整场缺失时记degraded(monkeypatch):
    day = dt.date(2026, 10, 13)
    monkeypatch.setattr(S, '_minute_day_empty', lambda d: True)
    monkeypatch.setattr(S, 'compute_day', lambda d: [
        {'security_code': '600000.SH', 'minute_available': False}])
    monkeypatch.setattr(S, 'write', lambda d, rows: len(rows))
    out = S.refresh(day)
    assert out['degraded'] is True and out['minute_missing'] == 1


# ------------------------------------------------------------------ 读侧诚实性

def test_读侧次日用的是lead而不是lag():
    src = open('duanxian/spike_view.py', encoding='utf-8').read()
    assert 'lead(pct_change) over' in src
    assert 'lag(pct_change)' not in src, 'lag 是前一天，读侧把"次日"算成"前一日"过（实测错过一次）'
    assert 'ln(1 + pct_change/100.0)' in src, '后 3/5 日要复利合成，不拿 close 自己除'


def test_统计每档都配了同期基线():
    src = open('duanxian/spike_view.py', encoding='utf-8').read()
    assert 'base as (' in src and '_BASE_COLS' in src
    # 基线必须在同一条 SQL 里一起交出去，不能靠前端记住一个数
    for k in ('bn', 'b_avg', 'b_win'):
        assert k in src
    assert 'where hit and n1 is not null' in src, '没有次日的事件不能进统计档（会把 n 灌水）'


def test_标签筛选认不得的要点名():
    from duanxian import spike_view as V
    picked, rejected = V._tags_arg('held5,不存在,fast')
    assert picked == ['held5', 'fast'] and rejected == ['不存在']
    assert set(V.TAG_KEYS) == set(S.TAG_LABELS), '标签清单两边走散了'


def test_tag_labels是中文标签不是列表的字符串切片():
    """浏览器实测抓到过的那一坨：`str(['slow','touched']).split(',')` 会交出带括号引号的碎片。"""
    from duanxian import spike_view as V
    out = V._labels_of(['slow', 'held5', '未知码'])
    assert out == ['慢拉（不足 4 个点）', '峰值后 5 分钟守住（回撤不足 1 个点）', '未知码']
    for x in out:
        assert '[' not in x and "'" not in x, out
    assert V._labels_of([]) == [] and V._labels_of(None) == []


def test_筛条件拼在limit之前():
    src = open('duanxian/spike_view.py', encoding='utf-8').read()
    body = src[src.index('def board('):src.index('def review(')]
    assert body.index('spike_tags && %s') < body.index('limit %s'), '在前端筛只能筛已交出的那些行'
    assert body.index('hi_pct >= %s') < body.index('limit %s')


def test_库里真的算过那些日子():
    """不打桩的一条：读回真库，确认起止与场数（"已回算 N 场"必须回查，rc=0 与日志都会说谎）。"""
    from app.db import engine
    r = engine.query_one(
        "select count(distinct trade_date) sessions, min(trade_date)::text a, "
        "max(trade_date)::text z, count(*) n, "
        "count(*) filter (where not minute_available) nm "
        "from analytics.intraday_spike where spec_version = %s",
        (S.SPEC_VERSION,), role='reader')
    assert r and r['n'] > 0, '表里一条异动都没有 —— 上面的单测全绿也等于没接上'
    assert r['sessions'] >= 10, f'只算了 {r["sessions"]} 场：{r["a"]} ~ {r["z"]}'
    assert r['nm'] == 0, f'{r["nm"]} 行没有分钟级读数，回查是不是哪天分钟线没入库'
    assert r['z'] == engine.scalar("select max(trade_date)::text from market_data.stock_daily"), \
        '异动没算到最新一场（每日加工链那一步没接上或被跳过）'
