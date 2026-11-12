# -*- coding: utf-8 -*-
"""近期各量化方案的名次与趋势带动（`duanxian/scheme_radar.py` ＋ 端点 `/api/quant/scheme-radar`）。

用户 2026-11-09 点名的这一块有四套判据，每一套都要单独钉住，因为它们在屏上都是**一句话**，
错了没有人看得见：

1. **窗口＝最近 w 场这段日历**，不是「凑够 w 个有值的场次」；x 坐标＝场次序号，缺值跳过但不重编号。
2. **点数不足一个数都不交**（交 0 等于把「没数」排成「很差」），2 场那一档只要 2 个点且不交标准误。
3. **带动＝Δ ÷ 长期斜率自己的标准误**，九档判据；任一档缺数只能念算不出，不许猜一个状态。
4. **名次必须连母体一起念**：四族是三把不同的尺；分位是屏幕上那两个数推出来的，不念库里 `percent_rank`。

外加两条真库不打桩的：四族的分母各自独立数一遍；XY 那一段的斜率用一份最直白的循环再拟合一次逐位对上。
"""
import itertools
import json
from pathlib import Path

import pytest

from duanxian import pg, scheme_radar as M, ttlcache

ROOT = Path(__file__).resolve().parents[1]
CODE = '600519.SH'


# ───────────────────────────────────────────────── 门槛点数

def test_两场那一档的门槛是二不是三():
    """2 场档按定义只有两个点：门槛 3 会让它永远交不出数，带动那几档跟着全废。"""
    assert M.min_points(1) == 2 and M.min_points(2) == 2
    assert M.min_points(3) == 3 and M.min_points(5) == 3
    assert [M.min_points(w) for w in M.WINDOWS] == [2, 3, 4, 10, 20]


def test_屏上那几句门槛话与代码同一个口径():
    """描述符里曾只念 `max(3, ⌈w/3⌉)`：2 场档于是被说成一个它自己达不到的门槛。"""
    src = (ROOT / 'duanxian/scheme_radar.py').read_text(encoding='utf-8')
    assert 'min_points(w) = max(3' not in src
    assert '2 场' in M.RULES['window'] and 'max(3, ⌈w/3⌉)' in M.RULES['window']
    assert any('2 场' in g['body'] for g in M.GUIDE)


# ───────────────────────────────────────────────── 最小二乘

def test_点数不足一个数都不交而不是交零():
    for n in (0, 1):
        st = M.slope_stats([(float(i + 1), 5.0) for i in range(n)])
        assert st['n'] == n
        assert st['slope'] is st['intercept'] is st['r2'] is st['slope_se'] is None
        assert f'只有 {n} 个点' in st['why']


def test_两点定一条线只交方向没有残差():
    st = M.slope_stats([(1.0, 2.0), (3.0, 8.0)])
    assert (st['n'], st['slope'], st['intercept'], st['r2']) == (2, 3.0, -1.0, 1.0)
    assert st['slope_se'] is None, '两点线没有残差可言：标准误必须是空，不是 0'


def test_完美直线的标准误是零所以带动判不出来而不是无穷大():
    """se＝0 当分母会给出天文数字的 z，屏上念成「带动得厉害」——那只是一条完全拟合的线。"""
    st = M.slope_stats([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0)])
    assert st['slope'] == 1.0 and st['slope_se'] == 0.0
    prev = M.slope_stats([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
    assert M.carry_state(st, st, prev)['state'] == 'unknown'


def test_场次序号没有变化时不交斜率():
    st = M.slope_stats([(2.0, 1.0), (2.0, 9.0)])
    assert st['slope'] is None and '序号没有变化' in st['why']


# ───────────────────────────────────────────────── 窗口取哪几个点

def _seq(points):
    """把 {场次序号: 值} 造成长接口要的 [(x, y, 日期)]；日期按 x 编号，一眼看得出卖了哪几场。"""
    return [(float(x), float(y), f'D{x:03d}') for x, y in sorted(points.items())]


def test_窗口是这段日历而不是凑够w个有值的场次():
    """真缺陷的回归钉：取「最后 w 个有值的点」会把稀疏那族的窗口悄悄拉长到整条日历，
    那条线就不再是近 10 场趋势，而且斜率会把头几场的老点一起算进去。"""
    pts = {1: 90.0, 2: 80.0, 3: 70.0, 4: 60.0, 5: 50.0}
    pts.update({x: float(x) for x in range(56, 61)})
    st = M.window_stats(_seq(pts), 10)
    assert st['points_used'] == 5, '近 10 场这段日历里只有 5 场有值：就用这 5 个，不去前面凑'
    assert (st['from'], st['to']) == ('D056', 'D060')
    assert st['usable'] is True and st['sparse'] is True, '5 个点过门槛但少于 10 场 ⇒ 稀疏'
    assert st['slope'] == pytest.approx(1.0), '斜率只由那 5 个点定，掺进老点就不是这个数了'


def test_往前挪s场取的是上一段同样长的日历():
    seq = _seq({x: float(x) for x in range(41, 61)})
    now, prev = M.window_stats(seq, 10), M.window_stats(seq, 10, shift=5)
    assert (now['from'], now['to']) == ('D051', 'D060')
    assert (prev['from'], prev['to']) == ('D046', 'D055'), 'Δ 比的是同长的上一段，不是少几场的那段'


def test_点数不足时斜率交空并把门槛念在why里():
    st = M.window_stats(_seq({56: 1.0, 58: 2.0, 59: 3.0}), 30)
    assert st['usable'] is False and st['points_used'] == 3
    assert st['slope'] is st['r2'] is st['slope_se'] is None
    assert '门槛是 10 个点' in st['why']


def test_两点档交得出数且why说明的是标准误是空的():
    st = M.window_stats(_seq({59: 1.0, 60: 2.0}), 2)
    assert st['usable'] is True and st['no_se'] is True and st['sparse'] is False
    assert st['slope'] == 1.0 and st['slope_se'] is None
    assert '两点定一条线' in st['why'] and '标准误是空的' in st['why']


# ───────────────────────────────────────────────── 带动判定

def _w(slope, se, window=5):
    return {'slope': slope, 'slope_se': se, 'window': window}


def test_带动的九档逐档造得出且每档都有一条能手算的式子():
    # (短期, 长期现在, 长期上一段, se, 期望)：Δ＝现在−上一段，z＝Δ÷se，flat＝|长期| < 0.5×se
    cases = [
        (0.3, 1.0, 0.5, 0.4, 'accelerating'),      # Δ=+0.5 z=1.25 同向且朝短期方向
        (0.3, 1.0, 0.72, 0.4, 'hint'),             # Δ=+0.28 z=0.70 过了苗头线、没到看得见
        (0.3, 1.0, 0.9, 0.4, 'silent'),            # Δ=+0.1 z=0.25 差在噪声量级内
        (0.3, 0.3, -0.1, 0.4, 'turned_over'),      # Δ=+0.4 z=1.00 短期把长期推过了 0
        (-0.3, 0.3, -0.1, 0.4, 'turned_own'),      # 同样跨过 0，但 Δ 不朝短期方向 ⇒ 不许算带动
        (0.3, -1.0, -0.4, 0.4, 'opposite_stuck'),  # Δ=−0.6 朝短期反方向 ⇒ 长期还没被推
        (-0.3, -1.0, 0.5, 0.4, 'turned_over'),     # Δ=−1.5 z=−3.75：负方向跨过 0，短期也朝负
        (0.3, -1.0, -2.0, 0.4, 'dragging_slow'),   # Δ=+1.0 z=2.5 顺短期方向往回拉，但没跨过 0
        (0.3, 0.1, 0.9, 0.4, 'turned_flat'),       # |长期 0.1| < 0.2 ⇒ 先判长期已经走平
    ]
    got = [M.carry_state(_w(ss, 0), _w(sl, se), _w(sp, se))['state']
           for ss, sl, sp, se, _ in cases]
    assert got == [c[4] for c in cases], list(zip(got, [c[4] for c in cases]))


def test_拐头这一档先于同向反向分档否则被念成加速():
    """用户点名的就是这一种：近 5 日在往上，把近 10 日的往下带动成往上。
    先按「短期与长期同向⇒加速」分档会把它念成同向加速，把已经拐头这件事说没了。"""
    own = M.carry_state(_w(0.936, 0), _w(-0.5189, 0.4444), _w(0.0493, 0.4444))
    assert own['state'] == 'turned_own'
    up = M.carry_state(_w(0.3, 0), _w(0.6, 0.2), _w(-0.4, 0.2))
    assert up['state'] == 'turned_over', '短期与长期都朝上、长期刚从负跨过 0 ⇒ 带动到位'
    assert (up['z'], up['delta']) == (5.0, 1.0)


def test_任一档缺数一律算不出不猜状态():
    base, prev = _w(1.0, 0.4), _w(0.5, 0.4)
    for bad_short in (None, _w(None, 0.4)):
        assert M.carry_state(bad_short, base, prev)['state'] == 'unknown'
    assert M.carry_state(base, None, prev)['state'] == 'unknown'
    assert M.carry_state(base, prev, None)['state'] == 'unknown'
    for no_se in (None, 0.0):
        assert M.carry_state(base, _w(1.0, no_se), prev)['state'] == 'unknown'
    unk = M.carry_state(None, base, prev)
    assert unk['label'] == M.CARRY_LABEL['unknown'] and unk['z'] is unk['delta'] is None


def test_带动图例每档都是短词加整句():
    """屏上那一格只念冒号前那个短词（十列宽摆不下整句），抽屉与悬停念整句。
    少一个冒号，那一格就会把 14 个字的解释原样贴进表里（2026-11-09 浏览器实测就是这样）。"""
    for st, label in M.CARRY_LABEL.items():
        assert '：' in label, st
        word, _, rest = label.partition('：')
        assert 1 <= len(word) <= 6, f'{st} 的屏上词太长：{word}'
        assert len(rest) >= 4, f'{st} 少了整句解释：{label}'
        assert ' **' not in label and '**' not in label, st


def test_图例里每一档都造得出来():
    """图例里留一档代码永远出不来的状态＝屏上那句解释是多余的假话；上一版就是这么藏的。"""
    reachable = set()
    for ss, sl, sp in itertools.product((None, -0.5, -0.05, 0.05, 0.5),
                                        (-1.0, -0.2, -0.02, 0.02, 0.5, 1.0),
                                        (-1.0, -0.2, -0.02, 0.02, 0.5, 1.0)):
        reachable.add(M.carry_state(_w(ss, 0), _w(sl, 0.4), _w(sp, 0.4))['state'])
    assert reachable == set(M.CARRY_LABEL), sorted(set(M.CARRY_LABEL) - reachable)


def test_距拐头只在真的往回拉时才算且太远的和不交():
    slow = M.carry_state(_w(0.3, 0, window=5), _w(-1.0, 0.4), _w(-1.8, 0.4))
    assert slow['state'] == 'dragging_slow'
    assert slow['turns_left'] == 6.2, '|长期 1.0| ÷（|Δ 0.8| ÷ 5 场）＝ 6.25 场'
    far = M.carry_state(_w(0.3, 0, window=5), _w(-1.0, 0.04), _w(-1.05, 0.04))
    assert far['state'] == 'dragging_slow' and far['turns_left'] is None, '每场只挪 0.01 ⇒ 100 场，超上限'
    acc = M.carry_state(_w(0.3, 0, window=5), _w(1.0, 0.4), _w(0.5, 0.4))
    assert acc['state'] == 'accelerating' and acc['turns_left'] is None, '没在往回拉就不算拐头距离'


# ───────────────────────────────────────────────── 名次、分位、缺数清洗

def test_名次差任何一边没数就交空而不是零():
    assert M.rank_delta(5, 3) == 2 and M.rank_delta(3, 5) == -2 and M.rank_delta(4, 4) == 0
    assert M.rank_delta(None, 3) is None and M.rank_delta(3, None) is None


def test_分位只用屏幕上那两个数推():
    assert M._share(1, 5573) == 99.98
    assert M._share(8, 5573) == 99.86
    assert M._share(62, 179) == 65.36
    for bad in ((None, 100), (5, 0), (5, None), (5, -1)):
        assert M._share(*bad) is None, bad


def test_nan文本与布尔不许带着进JSON():
    """pandas 把 NULL 落在文本列上给的是 nan，而 `bool(nan)` 是 True：
    直接念就会把「库里没写这一列」说成「这一格是旧的」，nan 进 JSON 浏览器当场炸。"""
    nan = float('nan')
    assert M._text(nan) is None and M._text(None) is None and M._text('Q1') == 'Q1'
    assert M._flag(nan) is None and M._flag(None) is None
    assert M._flag(True) is True and M._flag(False) is False and M._flag(1) is None


def _spec(key):
    return next(s for s in M.SERIES_SPEC if s['key'] == key)


def test_四种状态分开交没入选不等于没读数():
    assert M._cell(_spec('oper_score'), 'oper_score', '2026-11-06', None, None)['state'] \
        == 'out_of_calendar'
    assert M._cell(_spec('ambush_score'), 'ambush_score', '2026-11-06', None, None)['state'] \
        == 'not_selected', '埋伏分只收当天入选的票：没行是事实，不是缺数'
    assert M._cell(_spec('dbl_energy'), 'dbl_energy', '2026-11-06', None, None)['state'] \
        == 'out_of_calendar'
    assert M._cell(_spec('oper_score'), 'oper_score', '2026-11-06',
                   {'v': None, 'rk': 3, 'pct': 50.0}, None)['state'] == 'no_value'
    assert M._cell(_spec('oper_score'), 'oper_score', '2026-11-06',
                   {'v': float('nan'), 'rk': 3, 'pct': 50.0}, None)['state'] == 'no_value', \
        'NaN 分值不是 0 分，也不是第 3 名'


def test_有分布与没分布念的是两句话():
    row = {'v': 3.9278, 'rk': 8, 'pct': 99.87, 'qd': None, 'stale': None}
    no_sp = M._cell(_spec('oper_score'), 'oper_score', '2026-11-06', row, None)
    assert no_sp['rank_kind'] == 'unknown' and no_sp['share'] is None, \
        '分布没数出来时名次照念但出声；没有母体就交不出分位'
    with_sp = M._cell(_spec('oper_score'), 'oper_score', '2026-11-06', row,
                      {'universe': 5573, 'distinct_values': 5000, 'biggest_tie': 3,
                       'tie_at_value': 1})
    assert with_sp['state'] == 'ok' and with_sp['rank_kind'] == 'ok'
    assert (with_sp['rank'], with_sp['share']) == (8, 99.86)
    assert with_sp['quadrant'] is None and with_sp['stale'] is None
    deg = M._cell(_spec('oper_score'), 'oper_score', '2026-11-06', row,
                  {'universe': 5571, 'distinct_values': 1, 'biggest_tie': 5571,
                   'tie_at_value': 5571})
    assert deg['rank_kind'] == 'degenerate' and deg['rank'] is None and deg['share'] is None, \
        '全场同一个值时库里的第 1 名与 50.0 互相打脸：两个都不念'


def test_埋伏分与倍量那两族交出的名次各带自己的母体():
    amb = M._cell(_spec('ambush_score'), 'ambush_score', '2026-11-06',
                  {'v': 29.536, 'rk': 62, 'uni': 179, 'types': None, 'pass': None}, None)
    assert (amb['rank'], amb['universe'], amb['share']) == (62, 179, 65.36)
    assert '入选' in amb['rank_note'] and '不是同一把尺' in amb['rank_note']
    assert amb['types'] is None and amb['strict_pass'] is None, 'NULL 的文本/布尔不清洗就是非法 JSON'
    dbl = M._cell(_spec('dbl_energy'), 'dbl_energy', '2026-11-06',
                  {'v': 4.79, 'rk': 1961, 'uni': 5529}, None)
    assert dbl['share'] == 64.53 and '档表' in dbl['rank_note']
    xy = M._cell(_spec('xy_rank_sum'), 'xy_rank_sum', '2026-11-06',
                 {'rs': 513, 'uni': 5573, 'rx': 28, 'ry': 485, 'stale': False}, None)
    assert (xy['value'], xy['rank'], xy['share']) == (513.0, 513, 90.79)
    assert xy['stale'] is False and '并列只数这一块没取' in xy['rank_note']
    assert M._cell(_spec('xy_rank_sum'), 'xy_rank_sum', '2026-11-06',
                   {'rs': None, 'uni': 5573}, None)['state'] == 'no_value'


# ───────────────────────────────────────────────── 描述符与登记

def test_列头说明与母体句只有一处实现():
    assert list(M.DEFINITIONS) == [c['label'] for c in M.COLUMNS]
    assert all(c['says'] and c['label'] for c in M.COLUMNS)
    assert M.CALIBER == M.RULES['rank']
    assert set(M.RULES) >= {'sessions', 'window', 'slope', 'carry', 'rank', 'direction', 'not_this'}
    assert {s['key'] for s in M.SERIES_SPEC} == {
        'oper_score', 'stock_quality', 'env_resonance', 'temp_score', 'vol_temp', 'sector_heat',
        'market_mood', 'vol_stage', 'xy_rank_sum', 'dbl_energy', 'ambush_score'}
    assert all(s.get('says') and s.get('unit') and s.get('direction') for s in M.SERIES_SPEC)


def test_留屏的声明不许进抽屉白名单():
    """缺数／算不出／缓存年龄这三类声明一律留屏；能折进抽屉的只有那两条口径说明。
    名单本身要能在 `_caveats` 里查到，否则白名单是在给一条不存在的规定名。"""
    src = (ROOT / 'duanxian/scheme_radar.py').read_text(encoding='utf-8')
    caveats_src = src[src.index('def _caveats'):src.index('def board(')]
    emitted = {'radar_frame_failed', 'radar_stale_rows', 'radar_not_selected',
               'radar_window_short', 'radar_window_sparse', 'radar_rank_kind',
               'radar_trend_shape', 'radar_calendar'}
    assert all(f"'{code}'" in caveats_src for code in emitted), '声明名字与 _caveats 分叉了'
    assert M.FOLDABLE_CAVEAT_CODES == {'radar_stale_rows', 'radar_trend_shape'}
    for must_stay in ('radar_not_selected', 'radar_window_short', 'radar_window_sparse',
                      'radar_rank_kind', 'radar_calendar', 'radar_frame_failed'):
        assert must_stay not in M.FOLDABLE_CAVEAT_CODES, must_stay


def test_参数上下界只有一处():
    assert (M.SESSIONS_MIN, M.SESSIONS_DEFAULT, M.SESSIONS_MAX) == (2, 5, 10)
    assert list(M.WINDOWS) == [2, 5, 10, 30, 60]


def test_端点上下夹与本模块常量同源():
    """界面 5／接口收 0.05 那一族：上下夹写两份，改一处另一处静默 422。"""
    src = (ROOT / 'vr/app.py').read_text(encoding='utf-8')
    seg = src[src.index('@app.get("/api/quant/scheme-radar")'):]
    seg = seg[:seg.index('\n@app.')]
    assert f'ge={M.SESSIONS_MIN}, le={M.SESSIONS_MAX}' in seg, seg[:600]
    assert f'sessions: int = Query({M.SESSIONS_DEFAULT}' in seg
    assert 'scheme_radar.board(code, sessions=sessions, refresh=refresh)' in seg
    assert 'refresh: bool = Query(' in seg, '没有 refresh 参数⇒点「重新取数」拿不到新数'


def test_三份名单一律数组绑定():
    """`in (%s)` 拼逗号串会把整串当成一个参数绑：实测八项能力 0 行、
    报 invalid input syntax for type date——「没读到」于是被界面念成「这一族没有分」。"""
    for name in ('_SCORE_SQL', '_DIST_SQL', '_TIE_SQL', '_XY_SQL', '_DBL_SQL', '_AMB_SQL'):
        sql = getattr(M, name)
        assert 'in (%s)' not in sql and 'in ([' not in sql, name
    assert 'any(%s::text[])' in M._SCORE_SQL
    assert 'any(%s::date[])' in M._DIST_SQL and 'any(%s::text[])' in M._DIST_SQL
    assert 'any(%s::date[])' in M._TIE_SQL and 'any(%s::text[])' in M._TIE_SQL
    assert "'xyr-1'" in M._XY_SQL, '合计名次只认自己那一版口径'


def test_名次线筛在窗口之后不许被本票筛参污染():
    """rank() 的窗口里一旦带上本票的筛参，名次就变成「只在这只票内部排」，
    母体那一个数会一直是 1——同一族缺陷在倍量榜的首次进榜日上真踩过。"""
    for name in ('_DBL_SQL', '_AMB_SQL'):
        sql = getattr(M, name)
        windowed = sql[:sql.index('select d, v, rk, uni')]
        assert 'rank() over (partition by trade_date' in windowed, name
        assert 'security_code = %s' not in windowed and 'code = %s' not in windowed, name
        assert 'nulls last' in windowed, name
        assert sql.rstrip().endswith('order by d'), name
    assert 'count(*) over (partition by trade_date)::int uni' in M._DBL_SQL, \
        '倍量的母体＝当天档表里有加权分的只数，不是全市场'


def test_缓存键带口径版本与场次数():
    """换场次数不换键＝读到的还是上一档参数的序列；键里没有 spec_version＝改版后念旧数。"""
    assert f'scheme_radar:{M.SPEC_VERSION}:{CODE}:5' == f'scheme_radar:radar-1:{CODE}:5'
    assert M.TTL == 3600


def test_缺参数时只交原因不交空表():
    out = M.board('')
    assert out.get('available') is False and '必须先选一只票' in out['reason']
    assert 'series' not in out


# ───────────────────────────────────────────────── 真库（不打桩）

@pytest.fixture(scope='module')
def radar_board():
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    ttlcache.clear('scheme_radar:')
    out = M.board(CODE, sessions=5)
    if not out.get('available'):
        pytest.skip(f'本机库交不出这一块：{out.get("reason")}')
    return out


def test_真库四族的分母各自独立数一遍(radar_board):
    """三把尺各数各的：八项是当日全场、倍量是当日档表、埋伏是当日入选批、XY 是当天参与排名的只数。
    分母念错，那一格的「第 N 名」与分位就全是假数。"""
    d = radar_board['as_of']
    by_key = {s['key']: s for s in radar_board['series']}
    one = lambda k: next(c for c in by_key[k]['cells'] if c['date'] == d)  # noqa: E731

    def count(sql, params):
        return int(pg.fetch_df(sql, params).to_dict('records')[0]['n'])

    cell = one('oper_score')
    assert cell['universe'] == count(
        "select count(*) n from analytics.score where trade_date = %s::date "
        "and capability_key = 'oper_score' and value is not null", (d,))
    assert cell['share'] == M._share(cell['rank'], cell['universe'])
    assert one('xy_rank_sum')['universe'] == count(
        "select count(*) n from analytics.xy_rank_daily where trade_date = %s::date "
        "and spec_version = 'xyr-1' and rank_sum is not null", (d,))
    assert one('dbl_energy')['universe'] == count(
        "select count(*) n from analytics.volume_double "
        "where trade_date = %s::date and dbl_energy is not null", (d,)), '倍量的尺只含有加权分的那些只'
    amb = one('ambush_score')
    if amb['state'] == 'ok':
        assert amb['universe'] == count(
            "select count(*) n from analytics.ambush_setup where trade_date = %s::date", (d,)), \
            '埋伏的尺＝当日入选那批票，不是全场'
    else:
        assert amb['state'] == 'not_selected', '这一族只有没入选／有读数两种说法'


def test_真库XY那一段斜率与独立最小二乘逐位对上(radar_board):
    """取数共用，拟合这一段用最直白的循环重算：验的是窗口取点、x 按场次编号、名次取负三件事。"""
    days = M._days(max(M.WINDOWS))
    cal = radar_board['calendar']
    assert (days[0], days[-1], len(days)) == (cal['from'], cal['to'], cal['sessions'])
    sr_of = {dd: i + 1 for i, dd in enumerate(days)}
    rows = pg.fetch_df("select trade_date::text d, rank_sum r from analytics.xy_rank_daily "
                       "where security_code = %s and spec_version = 'xyr-1' "
                       "and trade_date >= %s::date order by d",
                       (radar_board['code'], cal['from'])).to_dict('records')
    pts = [(float(sr_of[r['d']]), -float(r['r'])) for r in rows
           if r['r'] is not None and r['d'] in sr_of]
    xy = next(s for s in radar_board['series'] if s['key'] == 'xy_rank_sum')
    hi = float(len(days))
    for st in xy['trend']:
        seg = [(x, y) for x, y in pts if hi - st['window'] < x <= hi]
        assert st['points_used'] == len(seg), f'近{st["window"]}场这段日历里独立数出 {len(seg)} 个点'
        if not st['usable']:
            assert st['slope'] is None
            continue
        n = len(seg)
        mx = sum(x for x, _ in seg) / n
        my = sum(y for _, y in seg) / n
        sxx = sum((x - mx) ** 2 for x, _ in seg)
        slope = sum((x - mx) * (y - my) for x, y in seg) / sxx
        assert st['slope'] == pytest.approx(round(slope, 4), abs=1e-4), f'近{st["window"]}场斜率对不上'


def test_真库那一屏交得出可用的JSON且声明齐备(radar_board):
    d = radar_board
    raw = json.dumps(d, ensure_ascii=False)
    assert 'NaN' not in raw and 'Infinity' not in raw, 'pandas 的 NaN 进 JSON 浏览器当场炸'
    assert '**' not in raw, '散文字段里的裸星号会原样念到屏上'
    states = {c['state'] for s in d['series'] for c in s['carry']}
    assert states <= set(M.CARRY_LABEL), f'屏上图例查不到的状态：{states - set(M.CARRY_LABEL)}'
    kinds = {c.get('rank_kind') for s in d['series'] for c in s['cells'] if c.get('rank_kind')}
    assert kinds <= {'ok', 'coarse', 'degenerate', 'no_rank', 'unknown'}
    codes = {c['code'] for c in d['caveats']}
    assert all(c.get('label') and c.get('text') for c in d['caveats'])
    assert 'radar_calendar' in codes, '日历长度那一句永远该交：它说的是这块最多能回看几场'
    assert all(str(c['code']).startswith('radar_') and c.get('label') for c in d['caveats'])
    assert d['generated_at'] and d['cache_key'].startswith('scheme_radar:')
    assert len(d['series']) == 11 and len(d['series'][0]['carry']) == 10
    # 四态逐态计数与逐态那句解释都从后端出：页面合并成一个总数就是那条红线的反面
    counts = d['state_counts']
    assert sum(counts.values()) == sum(len(s['cells']) for s in d['series'])
    assert counts['ok'] > 0 and set(counts) == {M.STATE_OK, M.STATE_NOT_SELECTED,
                                                M.STATE_NO_VALUE, M.STATE_OUT_OF_CALENDAR}
    assert set(d['cell_states']) == set(counts)
    for st, sentence in d['cell_states'].items():
        if st != M.STATE_OK:
            assert sentence, f'{st} 那一态没有句子，屏上就只剩一个数'
    assert all(len(s['trend']) == len(M.WINDOWS) for s in d['series'])


def test_真库命中缓存时交出发射时间与年龄(radar_board):
    ttlcache.clear('scheme_radar:')
    first = M.board(CODE, sessions=5)
    second = M.board(CODE, sessions=5)
    assert first['cached'] is False, '清完键第一发必须是现算，不许把缓存里别的参数当自己的'
    assert second['cached'] is True and second['generated_at'] == first['generated_at']
    assert second['cache_age_seconds'] <= 5
    assert second['series'] == first['series'], '命中缓存交的是同一份数，不是重算一遍看着一样'
    assert 'elapsed_ms' in second and 'spec_version' in second
    ttlcache.clear('scheme_radar:')
