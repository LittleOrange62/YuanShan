# -*- coding: utf-8 -*-
"""埋伏票（左栏）判据 + 左右两栏读侧的回归。

判据只住 `app/compute/ambush_setup.py` 一份，这里钉四件事：

① **缺数不当 0**：均线少一根就交 None（拿缺的那根当 0 会把极差算成假小），
   合成分里缺的那一块从分母剔掉，全缺交 None 而不是 0 分；
② **不完整周不参与重心连升**（节假日周只有 1~4 场）—— 而"要几个完整周"这个阈值
   本身被国庆那批周桶证过：定成 8 会当场把严档清空（严档恒 0 只），所以钉在这里；
③ **类型多选必须在库里筛**（拼在 `order by … limit` 之前），每类"全场实有几只"
   走一条不带筛选、不带 limit 的聚合；读不到交 null，不拿 0 顶（0 = 真的没有）；
④ **屏幕上那一格读到的字，就是排序排的那个字**：右栏原先按 `signal_type`
   （first_break/converging…）排、显示中文标签，点它行序看不懂；现在行里带
   `signal_type_label`，标签只在计算模块写一份。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.compute import ambush_setup as A
from app.compute import breakout_signal as B
from app.compute import stock_character as CH
from duanxian import pg, quant


# ================================================================ ① 纯判据（不碰库）

def test_均线少一根交_none_而不是把缺的那根当零():
    assert A.ma_spread_pct([10, 10, 10, 10, 10]) == 0.0
    assert A.ma_spread_pct([11, 10, 10, 10, 10]) == 10.0
    assert A.ma_spread_pct([10, 10, 10, 10, None]) is None
    assert A.ma_spread_pct([10, 10, 10, 10, 0]) is None   # 0 不是均线价，剔掉后只剩 4 根
    assert A.ma_spread_pct([]) is None


def test_周线只认完整周参与连升但把不完整周的场数一起交出去():
    # (周均价, 该周交易日数)，倒序：最近在前
    m = A.weekly_metrics([(10.0, 5), (9.0, 5), (8.0, 5)])
    assert (m['weekly_streak'], m['weekly_ups6'], m['weekly_bars']) == (2, 2, 3)
    assert m['week_partial'] is False

    # 当前周只有 2 场（节假日）：不参与比较，但场数要看得见，界面靠它自证
    p = A.weekly_metrics([(99.0, 2), (10.0, 5), (9.0, 5), (8.0, 5)])
    assert (p['weekly_streak'], p['weekly_ups6'], p['weekly_bars']) == (2, 2, 3)
    assert p['week_sessions_cur'] == 2 and p['week_partial'] is True

    assert A.weekly_metrics([])['weekly_ups6'] is None
    assert A.weekly_metrics([(10.0, 5)]) == {'weekly_streak': None, 'weekly_ups6': None,
                                             'weekly_bars': 1, 'week_sessions_cur': 5,
                                             'week_partial': False}


def test_严档要几个完整周是七不是八_因为假期会把周桶切碎():
    """2026-10-12 实测：11 个周桶里 4 个是国庆造成的不完整周（1/2/3/4 场），
    满 5 场的只剩 7 个。阈值当时定成 8 ⇒ 严档恒 0 只 —— 那是跟假期过不去。"""
    assert A.WEEKLY_MIN_BARS == 7
    assert A.WEEKLY_UPS6_MIN == 4
    weeks = [(10.6, 5), (10.5, 5), (10.4, 5), (10.3, 5), (10.2, 5), (10.1, 5), (10.0, 5),
             (9.9, 4), (9.8, 3), (9.7, 2), (9.6, 1)]
    m = A.weekly_metrics(weeks)
    assert m['weekly_bars'] == 7 and m['weekly_ups6'] == 6


def test_合成分缺的那一块不进分母_也不按零计():
    full = {'ma_spread_pct': 0.0, 'weekly_ups6': 5, 'shrink_ratio': 0.4, 'box_pos_pct': 80.0}
    assert A.ambush_score(full) == 100.0
    # 只剩 tight + weekly：(40*1 + 25*1) / 65 * 100 = 100（分母也跟着缩）
    assert A.ambush_score({'ma_spread_pct': 0.0, 'weekly_ups6': 5,
                           'shrink_ratio': None, 'box_pos_pct': None}) == 100.0
    # 一项满分一项为零：40*1 + 25*0 除以 65
    assert abs(A.ambush_score({'ma_spread_pct': 0.0, 'weekly_ups6': 0,
                               'shrink_ratio': None, 'box_pos_pct': None}) - 61.538) < 0.01
    assert A.ambush_score({'ma_spread_pct': None, 'weekly_ups6': None,
                           'shrink_ratio': None, 'box_pos_pct': None}) is None


def _daily(**kw):
    base = {'bars_60': 80, 'close_hfq': 10.0, 'ma5': 10.0, 'ma10': 10.0, 'ma20': 10.0,
            'ma30': 10.0, 'ma60': 10.0, 'ma_spread_pct': 0.4, 'range20_pct': 8.0,
            'box_pos_pct': 60.0, 'trend60_pct': 20.0, 'dev_ma20_pct': 1.0,
            'dev_ma60_pct': 1.2, 'shrink_ratio': 0.7, 'obv_ratio': 0.1,
            'days_since_big': None, 'hold_vs_big_pct': None, 'post_low_ge': None,
            'big_day_date': None}
    return {**base, **kw}


_WEEK7UP4 = {'weekly_streak': 3, 'weekly_ups6': 4, 'weekly_bars': 7,
             'week_sessions_cur': 5, 'week_partial': False}


def test_判得出才算命中_判不出是_没有_不是_很差():
    hit = A.judge('600000.SH', _daily(), _WEEK7UP4)
    assert hit['strict_pass'] is True and 'sticky_pull' in hit['ambush_types']

    # 六根完整周不够数「近 6 周」：一条都不该命中（不是给它打个低分）
    thin = A.judge('600000.SH', _daily(), {**_WEEK7UP4, 'weekly_bars': 6})
    assert thin['strict_pass'] is False and 'sticky_pull' not in thin['ambush_types']

    # 满窗线（60 根）以下不判：MA60 与 60 日涨幅都不可信
    short = A.judge('600000.SH', _daily(bars_60=59), _WEEK7UP4)
    assert short['ambush_types'] == [] and short['strict_pass'] is False


def test_回踩与背离与龙回头各自要求的东西不同():
    pb20 = A.judge('1.SH', _daily(dev_ma20_pct=1.0, dev_ma60_pct=9.0, trend60_pct=20.0,
                                  shrink_ratio=0.7), {'weekly_streak': 0, 'weekly_ups6': 0,
                                  'weekly_bars': 7, 'week_sessions_cur': 5, 'week_partial': False})
    assert 'pullback_ma20' in pb20['ambush_types'] and 'pullback_ma60' not in pb20['ambush_types']

    obv = A.judge('1.SH', _daily(obv_ratio=0.40, trend60_pct=2.0), dict(_WEEK7UP4, weekly_ups6=0))
    assert 'obv_bid' in obv['ambush_types']

    dragon = A.judge('1.SH', _daily(days_since_big=12, hold_vs_big_pct=3.0, post_low_ge=True,
                                    big_day_date='2026-09-24', shrink_ratio=0.6), dict(_WEEK7UP4, weekly_ups6=0))
    assert 'dragon_back' in dragon['ambush_types']
    # 跌破过那根大阳的最低价 ⇒ 洗盘走坏了，不算回头
    broke = A.judge('1.SH', _daily(days_since_big=12, hold_vs_big_pct=3.0, post_low_ge=False,
                                   shrink_ratio=0.6), dict(_WEEK7UP4, weekly_ups6=0))
    assert 'dragon_back' not in broke['ambush_types']


def test_缺的读数写进_missing_字符串给界面点名():
    r = A.judge('1.SH', _daily(ma_spread_pct=None, obv_ratio=None, box_pos_pct=None), _WEEK7UP4)
    assert set(r['missing'].split(',')) == {'ma_spread_pct', 'obv_ratio', 'box_pos_pct'}


# ================================================================ 读侧：左栏


@pytest.fixture
def amb(monkeypatch):
    """按住左栏读侧的取数：这场有没有数、SQL 交出什么行、聚合交出什么数。"""
    cap = {'rows': [], 'counts': None, 'tot': {'n': 397, 'strict': 22},
           'char': {'n': 5586, 'scored': 5376}, 'one': {}}

    def _rows(sql, params=()):
        if 'unnest' in sql:
            cap['count_sql'] = sql
            cap['count_params'] = params
            if cap['counts'] is None:
                raise RuntimeError('模拟聚合炸了')
            return list(cap['counts'])
        cap['sql'] = sql
        cap['params'] = list(params)
        return list(cap['rows'])

    def _one(sql, params=()):
        # 股性那张表的"整场有没有行"是第二条 `_one`：桩必须按 SQL 分流，
        # 两边共用一份读数就永远测不出"埋伏有数、股性还没入库"那一种真实状态。
        if 'stock_character_daily' in sql:
            cap['char_sql'] = sql
            cap['char_params'] = tuple(params)
            return dict(cap['char'])
        return dict(cap['tot'])

    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(quant.pg, "session_has", lambda table, d: True)
    # 桩的签名要跟真函数一致：真的 `_latest(table, spec=None)` 现在带版本条件，
    # 桩只收一个位置参数就会在被调用时 TypeError（本项目钉过的那类"桩与契约分叉"）。
    monkeypatch.setattr(quant, "_latest", lambda table, spec=None: "2026-10-12")
    monkeypatch.setattr(quant, "_rows", _rows)
    monkeypatch.setattr(quant, "_one", _one)
    monkeypatch.setattr(quant, "_coverage", lambda tables: {})
    monkeypatch.setattr(quant, "_comments", lambda table: {})
    monkeypatch.setattr(quant, "_cache_caveat", lambda keys: [])
    monkeypatch.setattr(quant, "_caveats", lambda key: [])
    return cap


def _amb_row(code='600000.SH', types=('sticky_pull',), strict=True, spread=0.42,
             with_char=True):
    r = {'code': code, 'name': '测试股', 'board': '主板', 'close_hfq': 10.0,
         'ma_spread_pct': spread, 'range20_pct': 8.0, 'weekly_ups6': 5, 'weekly_streak': 3,
         'weekly_bars': 9, 'week_sessions_cur': 3, 'week_partial': True,
         'shrink_ratio': 0.7, 'box_pos_pct': 55.0, 'trend60_pct': 3.0, 'obv_ratio': 0.1,
         'days_since_big': None, 'hold_vs_big_pct': None, 'big_day_date': None,
         'ambush_types': list(types), 'strict_pass': strict, 'ambush_score': 71.0,
         'bars_60': 90, 'missing': None, 'spec_version': A.SPEC_VERSION,
         'calculated_at': None,
         'amp_10': 3.214, 'amp_10_pctile': 55.0, 'char_bars_10': 10,
         'char_level': 'middle', 'char_level_label': CH.CHAR_LEVEL_LABELS['middle'],
         'char_mid_pctile': 61.0, 'gene_pctile': 48.0, 'seal_250': 3, 'touch_250': 5,
         'consec_max_250': 2, 'char_bars_250': 240, 'char_missing': {},
         'char_spec': CH.SPEC_VERSION}
    if not with_char:          # 左连接拿不到行：那些键在，值全是 None（不是 0）
        for k in list(r):
            if k.startswith(('amp_', 'char_', 'seal_', 'touch_', 'consec_')):
                r[k] = None
    return r


def test_类型多选拼在limit之前而不是筛完再截断(amb):
    """在前端筛就只能筛到合成分最高的那 150 只：「这一类剩 3 行」会被读成全场只有 3 只
    （实测 2026-10-12 全场龙回头 106 只）。"""
    amb['rows'] = [_amb_row()]
    amb['counts'] = [{'t': 'dragon_back', 'n': 106}]
    out = quant.ambush(types='sticky_pull,dragon_back')
    sql = amb['sql']
    assert 'a.ambush_types && %s::text[]' in sql, sql[-400:]
    assert sql.index('a.ambush_types') < sql.index('order by'), "筛在排序之后 = 只是把交出的行删了几条"
    assert sql.index('a.ambush_types') < sql.index('limit'), "筛在 limit 之后 = 上限先砍掉了别的类"
    assert amb['params'][0] == CH.SPEC_VERSION, \
        '股性表左连接的版本条件是第一个参数（漏了它就会把旧批的股性盖上来）'
    assert amb['params'][1] == '2026-10-12'
    assert amb['params'][2] == ['sticky_pull', 'dragon_back']
    assert amb['params'][-1] == 150
    assert out['filter']['types'] == ['sticky_pull', 'dragon_back']
    assert out['filter']['types_rejected'] == []


def test_每一类全场实有几只走不带筛选不带limit的聚合(amb):
    amb['rows'] = [_amb_row()]
    amb['counts'] = [{'t': t, 'n': 10 + i} for i, t in enumerate(A.TYPE_LABELS)]
    out = quant.ambush(types='dragon_back', only_strict=True)
    csql = amb['count_sql']
    assert 'unnest' in csql and 'group by' in csql
    assert 'a.ambush_types &&' not in csql and 'strict_pass' not in csql, "计数不该带上被筛的那一档"
    assert 'limit' not in csql.lower(), "计数不能被取数上限截"
    assert amb['count_params'] == ('2026-10-12',), amb['count_params']
    opts = {o['value']: o for o in out['type_options']}
    assert [opts[t]['count'] for t in A.TYPE_LABELS] == [10, 11, 12, 13, 14]
    # 这一场真没有的类型：聚合成功时"没出现"就是 0 只
    amb['counts'] = [{'t': 'dragon_back', 'n': 106}]
    out2 = quant.ambush()
    o2 = {o['value']: o for o in out2['type_options']}
    assert o2['sticky_pull']['count'] == 0 and o2['dragon_back']['count'] == 106
    assert o2['obv_bid']['label'] == A.TYPE_LABELS['obv_bid'], "标签只住计算模块一份"
    assert o2['obv_bid']['meaning'] == A.TYPE_MEANINGS['obv_bid']


def test_计数那条读不到时交null而不是0(amb):
    """0 只与没读到是两句话：前者是这一场真的没有，后者会让人以为这一类消失了。"""
    amb['rows'] = [_amb_row()]
    amb['counts'] = None                     # 聚合抛异常
    out = quant.ambush()
    assert all(o['count'] is None for o in out['type_options'])
    assert 'ambush_counts_unread' in [c['code'] for c in out['caveats']]
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'ambush_counts_unread')
    assert '没读到' in txt and '不拿 0 顶' in txt


def test_认不得的类型不参与筛选并当场点名(amb):
    amb['rows'] = [_amb_row()]
    amb['counts'] = [{'t': 'dragon_back', 'n': 106}]
    out = quant.ambush(types='nope,龙回头')
    assert out['filter']['types'] == []
    assert out['filter']['types_rejected'] == ['nope', '龙回头']
    seg = amb['sql'].split('where')[1].split('order by')[0]
    assert '&& %s::text[]' not in seg, f"认不得就别把条件拼进 where：{seg}"
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'ambush_type_rejected')
    assert '没有参与筛选' in txt and 'sticky_pull' in txt


def test_严档与周线抬升的筛条件也拼在库里(amb):
    amb['rows'] = [_amb_row()]
    amb['counts'] = [{'t': 'sticky_pull', 'n': 22}]
    quant.ambush(only_strict=True, min_weekly_ups=5)
    seg = amb['sql'].split('where')[1].split('order by')[0]
    assert 'and a.strict_pass' in seg and 'coalesce(a.weekly_ups6, 0) >= %s' in seg, seg
    assert amb['params'] == [CH.SPEC_VERSION, '2026-10-12', 5, 150], amb['params']
    plain = quant.ambush()
    assert 'and a.strict_pass' not in amb['sql'], "不筛就不该把条件拼进 SQL"
    assert amb['params'] == [CH.SPEC_VERSION, '2026-10-12', 150], amb['params']
    assert plain['day_total'] == 397 and plain['day_strict_total'] == 22


def test_筛空了要说清是筛空的_不是库里没有埋伏票(amb):
    amb['rows'] = []                          # 库里 397 只，但这一档筛空
    amb['counts'] = [{'t': 'sticky_pull', 'n': 22}]
    out = quant.ambush(types='sticky_pull', only_strict=True)
    assert out['available'] is True and out['rows'] == []
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'ambush_empty')
    assert '397' in txt and '不是库里没有埋伏票' in txt, txt


# ================================================================ 读侧：右栏


@pytest.fixture
def bo(monkeypatch):
    cap = {'rows': [], 'counts': [{'t': 'first_break', 'n': 7},
                                  {'t': 'surge_after_shrink', 'n': 539},
                                  {'t': 'converging', 'n': 69}]}

    def _rows(sql, params=()):
        if 'group by 1' in sql:
            cap['count_sql'] = sql
            cap['count_params'] = list(params)
            if cap['counts'] is None:
                raise RuntimeError('模拟聚合炸了')
            return list(cap['counts'])
        cap['sql'] = sql
        cap['params'] = list(params)
        return list(cap['rows'])

    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(quant.pg, "session_has", lambda table, d: True)
    # 桩的签名要跟真函数一致：真的 `_latest(table, spec=None)` 现在带版本条件，
    # 桩只收一个位置参数就会在被调用时 TypeError（本项目钉过的那类"桩与契约分叉"）。
    monkeypatch.setattr(quant, "_latest", lambda table, spec=None: "2026-10-12")
    monkeypatch.setattr(quant, "_rows", _rows)
    monkeypatch.setattr(quant, "_one", lambda sql, params=(): {'n': 615})
    monkeypatch.setattr(quant, "_coverage", lambda tables: {})
    monkeypatch.setattr(quant, "_comments", lambda table: {})
    monkeypatch.setattr(quant, "_cache_caveat", lambda keys: [])
    monkeypatch.setattr(quant, "_caveats", lambda key: [])
    return cap


def _bo_row(code='301421.SZ', st='first_break', bars=100, win=40):
    return {'code': code, 'name': '测试股', 'board': '创业板', 'signal_type': st,
            'last_surge_date': '2026-07-01', 'surge_vol_ratio': 1.98, 'shrink_days': 6,
            'converge_days': 12, 'break_vol_ratio': 1.72, 'break_dev': 0.0213,
            'break_close': 30.1, 'break_ma60': 29.5, 'window_days': win, 'bars_used': bars}


def test_右栏各类实有几只不再从截断后的行里数(bo):
    """原先 `type_counts` 是把已交出的 150 行自己数一遍 ⇒ "首破 7 只"读起来像全场只有 7 只，
    其实是 150 行里恰好 7 行。现在这一格来自一条不带筛选、不带 limit 的聚合。"""
    bo['rows'] = [_bo_row()]
    out = quant.breakout_signal()
    csql = bo['count_sql']
    assert 'count(*)::int' in csql and 'group by 1' in csql
    assert 'signal_type = %s' not in csql and 'limit' not in csql.lower(), "计数不能被筛掉或截断"
    # 聚合也必须带判据版本：主键含 spec_version，不带就是把两个版本的行数加在一起当今天的数
    assert 'spec_version' in csql, "那条全场计数漏了版本条件"
    assert bo['count_params'] == ['2026-10-12', B.SPEC_VERSION], bo['count_params']
    opts = {o['value']: o for o in out['type_options']}
    assert opts['first_break']['count'] == 7 and opts['converging']['count'] == 69
    assert out['day_total'] == 615
    assert 'type_counts' not in out, "旧的「从行里数」那一格已经没了"


def test_屏幕上那一格读的字就是排序排的字(bo):
    """列头的 sortKey 是 `signal_type_label`，所以每一行必须带着这个键；
    它由计算模块的 TYPE_LABELS 翻出来 —— 页面里不许再有第二份中英对照。"""
    bo['rows'] = [_bo_row(st='first_break'), _bo_row('600001.SH', st='converging')]
    out = quant.breakout_signal()
    assert [r['signal_type_label'] for r in out['rows']] == ['首破', '收敛中']
    assert out['rows'][0]['signal_type'] == 'first_break', "原值留着，筛与悬停都用它"
    src = Path('frontend/src/pages/VolumeBreakout.tsx').read_text(encoding='utf-8')
    assert 'SIGNAL_LABELS' not in src, "前端那份中英对照必须删掉（标签只住计算模块）"
    assert 'sortKey="signal_type_label"' in src


def test_右栏每个列头的sortKey都是接口真交出的字段(bo):
    """写错 sortKey **不报错、也不换序**，aria-sort 还会照样亮 —— 只能在这里钉住：
    列头声明的键必须出现在读侧交出的行里。"""
    import re
    bo['rows'] = [_bo_row()]
    row_keys = set(quant.breakout_signal()['rows'][0])
    page = Path('frontend/src/pages/VolumeBreakout.tsx').read_text(encoding='utf-8')
    right = re.findall(r'sortKey="([a-z0-9_]+)"', page.split('function BreakoutBoard')[1])
    assert right, "右栏一个可排序列头都没有？"
    assert set(right) <= row_keys, set(right) - row_keys


def test_左栏每个列头的sortKey都是接口真交出的字段(amb):
    import re
    amb['rows'] = [_amb_row()]
    amb['counts'] = [{'t': 'sticky_pull', 'n': 22}]
    akeys = set(quant.ambush()['rows'][0])
    left = Path('frontend/src/components/quant/AmbushBoard.tsx').read_text(encoding='utf-8')
    lkeys = re.findall(r'sortKey="([a-z0-9_]+)"', left)
    assert lkeys and set(lkeys) <= akeys, set(lkeys) - akeys
    assert 'ambush_type_labels' in lkeys and 'ambush_type_labels' in left


def test_右栏阈值与标签取自计算模块而不是页面写死(bo):
    bo['rows'] = [_bo_row()]
    out = quant.breakout_signal()
    assert out['thresholds'] == {'surge_mult': B.SURGE_MULT, 'break_mult': B.BREAK_MULT,
                                 'dev_thresh': B.DEV_THRESH,
                                 'converge_min_days': B.CONVERGE_MIN_DAYS,
                                 'window_days': B.WINDOW_DAYS}
    assert out['window_days'] == B.WINDOW_DAYS
    assert [o['label'] for o in out['type_options']] == list(B.TYPE_LABELS.values())
    assert all(o['meaning'] == B.TYPE_MEANINGS[o['value']] for o in out['type_options'])


def test_右栏认不得的信号值不参与筛选并点名(bo):
    bo['rows'] = [_bo_row()]
    out = quant.breakout_signal(signal_type='shoupo')
    assert 'v.signal_type = %s' not in bo['sql'], "认不得就不拼进 SQL"
    # 主查询的参数：日期、判据版本、limit（版本条件在读侧不能漏，见 `where` 那段注释）
    assert bo['params'] == ['2026-10-12', B.SPEC_VERSION, 150], bo['params']
    assert out['filter']['signal_type'] == 'all'
    assert out['filter']['signal_type_rejected'] == ['shoupo']
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'breakout_type_rejected')
    assert '没有参与筛选' in txt and 'first_break' in txt

    quant.breakout_signal(signal_type='converging')
    assert 'v.signal_type = %s' in bo['sql']
    assert bo['params'] == ['2026-10-12', B.SPEC_VERSION, 'converging', 150], bo['params']


def test_右栏计数读不到时交null(bo):
    bo['rows'] = [_bo_row()]
    bo['counts'] = None
    out = quant.breakout_signal()
    assert all(o['count'] is None for o in out['type_options'])
    assert 'breakout_counts_unread' in [c['code'] for c in out['caveats']]


def test_满窗与短窗并排时必须点名(bo):
    bo['rows'] = [_bo_row(bars=25)]
    out = quant.breakout_signal()
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'short_window')
    assert '301421' in txt and '不能与满窗的票横比' in txt


# ================================================================ 不打桩：真库跑一次

def test_真库这场判出来的严档行确实过得了三条阈值():
    """判据类必须有一条不靠桩的：桩能证明"代码按写的走"，只有真库能证明"写的是对的"。
    库里没有埋伏行时跳过（回算还没铺满历史）。"""
    if not pg.enabled():
        pytest.skip('本机 PG 未就绪')
    out = quant.ambush(limit=20)
    if not out.get('available'):
        pytest.skip(f"这一场没有埋伏行可读：{out.get('reason')}")
    payload = json.dumps({'data': out}, allow_nan=False)   # NaN 一旦漏进出口，这里就炸
    assert payload
    strict = [r for r in out['rows'] if r['strict_pass']]
    for r in strict:
        assert r['ma_spread_pct'] < A.SPREAD_MAX
        assert r['range20_pct'] < A.RANGE20_MAX
        assert r['weekly_ups6'] >= A.WEEKLY_UPS6_MIN
        assert r['weekly_bars'] >= A.WEEKLY_MIN_BARS
    # 交出的行是被 limit 截过的，所以只有一条方向成立的比较：全场严档数 >= 页上这几行里的严档数
    assert out['day_strict_total'] >= len(strict), (out['day_strict_total'], len(strict))
    for r in out['rows']:
        assert r['ambush_types'], "命中的票至少有一类，否则这行不该存在"
        assert r['ambush_type_labels'] == [A.TYPE_LABELS[t] for t in r['ambush_types']]


# ================================================================ ⑤ 股性/振幅那两列（11-04）

def test_那两列是左连接另一张表_带版本条件_且不带进where(amb):
    """LEFT JOIN 不是 JOIN：股性那一族缺行时埋伏榜照样要交（那是另一张表的一步没跑，
    不是这些票不存在）。版本条件漏了就会把旧批的股性盖到今天的行上。"""
    amb['rows'] = [_amb_row()]
    amb['counts'] = [{'t': 'sticky_pull', 'n': 22}]
    quant.ambush()
    sql = amb['sql']
    assert 'left join analytics.stock_character_daily' in sql, sql[-900:]
    assert 'c.spec_version = %s' in sql, '左连接漏版本条件 = 旧批盖新批'
    assert 'c.trade_date = a.trade_date' in sql, '不按同一场连接就是把去年的股性念成今天的'
    seg = sql.split('where')[1].split('order by')[0]
    assert 'c.' not in seg, "筛条件里出现 c. 就把左连接变成了内连接（缺股性的票会整行消失）"


def test_左连接拿不到行时交null而不是零(amb):
    """`amp_10=None` 是「没数」，`0` 是「这 10 场一天都没波动过」——后者是真读数。"""
    amb['rows'] = [_amb_row(with_char=False)]
    amb['counts'] = [{'t': 'sticky_pull', 'n': 22}]
    r = quant.ambush()['rows'][0]
    assert r['amp_10'] is None and r['char_level'] is None and r['char_level_label'] is None
    assert r['seal_250'] is None and r['consec_max_250'] is None
    assert r['char_spec'] is None


def test_整场没入库时点名的是那一步没跑_而不是每只票判不了(amb):
    """埋伏有数、股性那一步没跑：这一场 `stockchar` 零行 ⇒ 出一条批级 caveat，
    界面上那两格是「—」并配一句「这一场的股性还没算」，绝不能让人以为埋伏榜坏了。"""
    amb['rows'] = [_amb_row()]
    amb['counts'] = [{'t': 'sticky_pull', 'n': 22}]
    amb['char'] = {'n': 0, 'scored': 0}
    out = quant.ambush()
    assert 'ambush_char_missing_batch' in [c['code'] for c in out['caveats']], out['caveats']
    txt = next(c['text'] for c in out['caveats'] if c['code'] == 'ambush_char_missing_batch')
    assert '还没入库' in txt and '不是「振幅 0」' in txt and 'stockchar' in txt, txt
    assert out['character']['has_rows_on_date'] is False
    assert out['character']['rows_on_date'] == 0
    # 那条点名用的聚合也必须带版本条件，否则它会说"有行"（读到旧批）而屏上全是空
    assert 'spec_version = %s' in amb['char_sql'] and amb['char_params'][1] == CH.SPEC_VERSION

    amb['char'] = {'n': 5586, 'scored': 5376}
    assert 'ambush_char_missing_batch' not in [c['code'] for c in quant.ambush()['caveats']]


def test_股性那两列的阈值与中文档名从写侧现读(amb):
    """界面不许抄第二份：档位门槛、窗口长度、`股性强/中/弱` 那一串都来自
    `app/compute/stock_character.py`（改一处就跟着动，同 `thresholds` 一条规矩）。"""
    amb['rows'] = [_amb_row()]
    amb['counts'] = [{'t': 'sticky_pull', 'n': 22}]
    out = quant.ambush()
    assert out['character']['spec_version'] == CH.SPEC_VERSION
    assert out['character']['table'] == CH.TABLE
    assert out['character']['level_labels'] == dict(CH.CHAR_LEVEL_LABELS)
    assert out['character']['amp_sessions'] == CH.AMP_SESSIONS
    assert out['character']['amp_min_bars'] == CH.AMP_MIN_BARS
    assert out['character']['char_sessions'] == CH.CHAR_SESSIONS
    assert out['character']['strong_min'] == CH.CHAR_STRONG_MIN
    assert out['character']['middle_min'] == CH.CHAR_MIDDLE_MIN
    cal = out['caliber']
    for token in (CH.CHAR_LEVEL_LABELS['strong'], CH.CHAR_LEVEL_LABELS['middle'],
                  CH.CHAR_LEVEL_LABELS['weak'], str(CH.AMP_MIN_BARS)):
        assert token in cal, f'口径句里少了 {token}（它是从写侧拼进去的，不是手抄）'
    assert '事后描述量' in cal and '不进埋伏分' in cal
    assert '**' not in cal, '口径句要上屏，裸 markdown 星号不许出现'


def test_两张表的列注释并交且同名的missing不互相覆盖(amb, monkeypatch):
    """`missing` 两张表都有：股性那份在这里改叫 `char_missing`。
    直接 update 会让其中一份注释盖掉另一份，界面悬停就念错表。"""
    amb['rows'] = [_amb_row()]
    amb['counts'] = [{'t': 'sticky_pull', 'n': 22}]
    monkeypatch.setattr(quant, '_comments', lambda table: {
        'missing': f'{table} 的缺数说明', 'amp_10': '振幅的口径', 'spec_version': '版本'})
    out = quant.ambush()
    d = out['definitions']
    assert d['missing'] == 'ambush_setup 的缺数说明', d['missing']
    assert d['char_missing'] == 'stock_character_daily 的缺数说明', d['char_missing']
    assert d['amp_10'] == '振幅的口径'


def test_覆盖体检同时交出两张表的起止(amb, monkeypatch):
    """埋伏榜现在读两张表，`coverage` 只报埋伏那张的话，股性滞后就没人看得见。"""
    seen = []

    def fake_cov(tables):
        seen.append(tuple(tables))
        return {t: {} for t in tables}

    monkeypatch.setattr(quant, '_coverage', fake_cov)
    amb['rows'] = [_amb_row()]
    amb['counts'] = [{'t': 'sticky_pull', 'n': 22}]
    out = quant.ambush()
    assert seen == [('ambush_setup', 'stock_character_daily')], seen
    assert 'stock_character_daily' in out['coverage']


def test_埋伏榜每一条caveat都带中文label_屏上不许念机器码(amb):
    """`Caveats` 那颗 chip 取的是 `label ?? code`（`DataPanel.tsx`）——
    四条埋伏声明（筛空／计数读不到／坏参数／股性整场没入库）都要在 `_CAV_LABEL` 里配好中文。
    走 `_cav()` 一处构造，别再手写 `{'code':…,'text':…}` 那种没有 label 的形状。"""
    import re as _re

    amb['rows'] = []                       # 筛空那一条
    amb['counts'] = None                   # 聚合读不到那一条
    out = quant.ambush(types='shoupo')     # 认不得的类型那一条
    amb['char'] = {'n': 0, 'scored': 0}    # 股性整场没入库那一条
    out2 = quant.ambush(types='shoupo')
    codes = {c['code'] for c in out['caveats']} | {c['code'] for c in out2['caveats']}
    assert {'ambush_type_rejected', 'ambush_counts_unread', 'ambush_empty',
            'ambush_char_missing_batch'} <= codes, codes
    for c in list(out['caveats']) + list(out2['caveats']):
        assert c.get('label'), f'{c["code"]} 没配 label —— 屏上那颗 chip 会念机器码'
        assert _re.search(r'[一-鿿]', c['label']), f'{c["code"]} 的 label 不是中文：{c["label"]}'
        assert c['label'] != c['code']
    assert set(quant._CAV_LABEL) >= codes, '埋伏那几条没进注册表（`_cav` 会退回中性词"口径说明"）'


# ================================================================ 不打桩：股性列与表里逐只一致

def test_真库交出的股性列与那张表逐只对得上():
    """左连接最容易悄悄接错的地方是**连到别的场次或别的版本**：
    这里拿接口交出的每一行回查库，四列逐个比。"""
    if not pg.enabled():
        pytest.skip('本机 PG 未就绪')
    out = quant.ambush(limit=40)
    if not out.get('available'):
        pytest.skip(f"这一场没有埋伏行可读：{out.get('reason')}")
    got = [r for r in out['rows'] if r.get('amp_10') is not None]
    if not got:
        pytest.skip('这一场股性表还没有行（回算没铺到、或 stockchar 那一步没跑）')
    from app.db import engine

    d = out['date']
    codes = [r['code'] for r in got]
    ref = {r['security_code']: r for r in (engine.query(
        "select security_code, amp_10, amp_10_pctile, char_level, char_level_label, "
        "       gene_pctile, seal_250, consec_max_250 "
        "  from analytics.stock_character_daily "
        " where trade_date = %s and spec_version = %s "
        "   and security_code = any(%s::text[])",
        (d, CH.SPEC_VERSION, codes), role='reader') or [])}
    for r in got:
        x = ref.get(r['code'])
        assert x is not None, f"{r['code']}：接口说这一场有股性行，库里按 {CH.SPEC_VERSION} 查不到"
        assert abs(float(x['amp_10']) - r['amp_10']) < 0.001, (r['code'], x['amp_10'], r['amp_10'])
        assert (x['char_level'] == r['char_level']
                and x['char_level_label'] == r['char_level_label']), (r['code'], x['char_level'])
        assert int(x['seal_250'] or 0) == (r['seal_250'] or 0), (r['code'], x['seal_250'])
        assert r['char_spec'] == CH.SPEC_VERSION
    # 中文档名是写侧那一份的键值，界面读到的词与库里的枚举必须同源
    for r in got:
        if r['char_level'] is not None:
            assert r['char_level_label'] == CH.CHAR_LEVEL_LABELS[r['char_level']]
        else:
            assert r['char_level_label'] is None, '判不了就是 None，不许补一个"股性弱"顶上去'

