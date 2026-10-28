# -*- coding: utf-8 -*-
"""名次稳定榜（近 3/7/11 场**全市场名次之和**从低到高，前 50）—— 用户 2026-10-15 点名。

钉的是四条会被读错、也会被"顺手改宽"的地方：

① **窗口不静默换**：只收 3/7/11 三档（界面上就是这三个按钮）。传 5 必须被拒，
   而且拒的是 `_reject`（"你给的条件我按不了"），不是 `_fail`（"库里没这场"）——
   混成一句会让人去查回填，真要改的是请求参数。
② **凑不满 N 场不进榜**：缺场次还去求和，等于让少加一项的票去和加满的票比大小
   （与「缺项不按 0 参与排名」同一条纪律）。被排除多少只必须随数据交出。
③ **派生列只算一次**：`avg_rank` = 和 ÷ 场次、`rank_spread` = 最差 − 最好、
   `board_rank` 从 1 连续。页面不重算，所以这里断了就是屏幕上那格断了。
④ **查询失败不许念成"这些场次没有名次"**（本项目犯过两次：探针兜空值 ⇒ 下游说一句假读数）。

最后两条**不打桩**：桩绿不代表接线对，真库里名次之和必须能复算、行序必须真的升序。
"""
import inspect

import pytest

from duanxian import quant as Q

STATS = {'sessions': 3, 'first_day': '2026-10-12', 'last_day': '2026-10-14',
         'universe_min': 5550, 'universe_max': 5562, 'stale_rows': 0}
COUNTS = {'codes': 5600, 'full_cov': 5540, 'partial': 60}


def _fake_board(*_a, **_k):
    return [
        {'security_code': '601398.SH', 'name': '工商银行', 'board': '沪主板',
         'sessions_used': 3, 'rank_sum': 6, 'best_rank': 2, 'worst_rank': 2,
         'latest_rank': 2, 'quadrant': 'Q1', 'oper_value': 0.812, 'rank_path': '2→2→2'},
        {'security_code': '601988.SH', 'name': '中国银行', 'board': '沪主板',
         'sessions_used': 3, 'rank_sum': 9, 'best_rank': 1, 'worst_rank': 6,
         'latest_rank': 6, 'quadrant': 'Q2', 'oper_value': 0.700, 'rank_path': '1→2→6'},
    ]


@pytest.fixture
def stub(monkeypatch):
    """打桩：只钉口径，不碰库。`calls` 记了几发查询，用来证明"被拒的那些根本没去查库"。"""
    calls = {'rows': 0, 'agg': 0}

    def fake_agg(key, sql, params=(), many=False):
        calls['agg'] += 1
        if key.startswith('rank_stability_stats'):
            return dict(STATS)
        if key.startswith('rank_stability_counts'):
            return dict(COUNTS)
        return {'a': '2026-01-27', 'b': '2026-10-14', 'n': 164}

    def fake_rows(sql, params=()):
        calls['rows'] += 1
        return _fake_board()

    monkeypatch.setattr(Q, '_agg', fake_agg)
    monkeypatch.setattr(Q, '_rows', fake_rows)
    monkeypatch.setattr(Q, '_latest', lambda table, spec=None: '20261014')
    monkeypatch.setattr(Q, '_labels', lambda: ({'Q1': '双高'}, '测试用标签'))
    monkeypatch.setattr(Q.pg, 'session_has', lambda table, d: True)
    monkeypatch.setattr(Q, 'clear_agg_cache', lambda prefix='': 0)
    return calls


def test_三档窗口各给一份数并交出窗口日期(stub):
    for n in Q.RANK_WINDOWS:
        d = Q.rank_stability(days=n, date='2026-10-14')
        assert d['available'], d.get('reason')
        assert d['window']['days'] == n
        assert d['window']['is_trading_days'] is True, \
            '「近 N 天」是交易日不是日历日，不交出这个旗标就会被读成日历日'
        assert d['window']['first_day'] == '2026-10-12' and d['window']['last_day'] == '2026-10-14'


def test_界面没有的窗口被拒且不查库(stub):
    """界面上只有 3/7/11 三个按钮：能传出一个界面问不出的窗口，
    就等于同一屏在不同人手里回答不同的问题。"""
    before = stub['rows']
    d = Q.rank_stability(days=5, date='2026-10-14')
    assert d['available'] is False
    assert '3 场' in d['reason'] and '7 场' in d['reason'] and '11 场' in d['reason']
    assert stub['rows'] == before, '被拒的参数根本不该发查询'
    # 而且是"参数不对"那一类，不许借"库里没这场"那句话
    assert '这一场没有数据' not in d['reason'] and '连不上' not in d['reason']


def test_派生列只算一次_页面读的就是这几格(stub):
    d = Q.rank_stability(days=3, date='2026-10-14')
    rows = d['rows']
    assert [r['board_rank'] for r in rows] == [1, 2], '榜内序号从 1 连续，页面不自己再数一遍'
    assert [r['avg_rank'] for r in rows] == [2.0, 3.0], '平均名次 = 和 ÷ 场次（场次不同的两只只能比这一列）'
    assert [r['rank_spread'] for r in rows] == [0, 5], '极差 = 最差 − 最好'
    assert rows[0]['rank_sum'] == 6 and isinstance(rows[0]['rank_sum'], int), \
        'bigint 不收成 int 会带 Decimal 进 JSON，浏览器那一侧解析直接炸'


def test_被排除的场次不静默消失(stub):
    d = Q.rank_stability(days=3, date='2026-10-14')
    u = d['universe']
    assert u['fully_covered'] == 5540 and u['excluded_partial'] == 60
    assert u['scored_codes'] == 5600
    assert '缺场次' in u['note'], '分母那句话必须跟着数交出，否则"和"会被读成全市场的数'


def test_聚合读不回来时不许念成这些场次没有名次(stub, monkeypatch):
    """检测器坏的时候不许装没事：None 会被下游写成一句看起来很合理的假读数。"""
    monkeypatch.setattr(Q, '_agg', lambda key, sql, params=(), many=False: None)
    d = Q.rank_stability(days=7, date='2026-10-14')
    assert d['available'] is False
    assert '不是这些场次没有名次' in d['reason']


def test_榜查询读不回来时同样出声(stub, monkeypatch):
    monkeypatch.setattr(Q, '_rows', lambda sql, params=(): None)
    d = Q.rank_stability(days=7, date='2026-10-14')
    assert d['available'] is False and '查询没读回来' in d['reason']


def test_凑不满窗口就进不了榜是SQL里的条件不是页面筛的():
    """这条纪律写在 SQL 的 `where` 上（`sessions_used = 窗口场数`），
    挪到页面或删掉都会让"少加一项"的票冲到榜首。"""
    src = inspect.getsource(Q.rank_stability)
    assert 'g.sessions_used = (select count(*) from w)' in src
    assert 'string_agg(rank_in_market::text' in src, \
        '逐日名次路径必须留在同一次聚合里：改回相关子查询实测 3 场 21 秒、11 场撞 statement timeout'


def test_端点已接上且参数范围与后端一致():
    # 只读文本不导入：`vr/app.py` 与摄取层的顶层 `app/` 包抢同一个裸名（见 test_core_logic.py:1746）
    import pathlib
    src = pathlib.Path('vr/app.py').read_text(encoding='utf-8')
    assert '@app.get("/api/quant/rank-stability")' in src
    assert 'quant.rank_stability(' in src, '路由写了却不调用实现＝假端点'
    assert "RANK_WINDOWS" in inspect.getsource(Q.rank_stability)


# ---------------------------------------------------------------- 不打桩的两条

def test_真库_名次之和能按源表复算():
    """10-15 实测：`analytics.score` 164 场、每场 5,55x 只、名次 1…N 连续无 NULL。
    这一条把榜顶那只票的"和"按源表逐场加一遍对回去 —— 读侧算错就是整屏算错。"""
    try:
        d = Q.rank_stability(days=3, limit=10)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f'本机库不可用：{e}')
    if not d.get('available'):
        pytest.skip(f'本机库这一场不可读：{d.get("reason")}')
    rows = d['rows']
    assert rows, '真库里近 3 场凑满的票不可能为 0 只'
    assert rows[0]['rank_sum'] == min(r['rank_sum'] for r in rows), '榜必须按和升序'
    code = rows[0]['security_code']
    check = Q._one("""with w as (select trade_date from (
                        select distinct trade_date from analytics.score
                         where capability_key = 'oper_score' and rank_in_market is not null
                         order by 1 desc limit 3) x)
                     select sum(s.rank_in_market)::bigint s, count(*)::int n
                       from analytics.score s join w using (trade_date)
                      where s.capability_key = 'oper_score' and s.security_code = %s""", (code,))
    assert check and int(check['n']) == 3, f'{code} 近 3 场都有名次才对得上'
    assert int(check['s']) == rows[0]['rank_sum'], \
        f"源表逐场加是 {int(check['s'])}，榜上交的是 {rows[0]['rank_sum']}"
    assert rows[0]['rank_path'].count('→') == 2, '三场就是两个箭头；段数不对说明路径与和不是同一批行'


def test_真库_窗口跨到没有行业广度分项的天要说和不可比():
    """名次历史已回算到 2025-08-28，而 `sector_guangdu` 分项只从 2026-07-29 起才有
    ⇒ 跨那条线的窗口把"少一个分项算出的 Y 轴"与"齐全的 Y 轴"加进同一个和里。
    和照样能算，但那句话必须随数交出，否则读者以为 11 场的名次同尺可比。"""
    try:
        d = Q.rank_stability(days=11, limit=10, date='2026-08-06')
    except Exception as e:  # noqa: BLE001
        pytest.skip(f'本机库不可用：{e}')
    if not d.get('available'):
        pytest.skip(f'本机库这一场不可读：{d.get("reason")}')
    u = d['universe']
    assert u['breadth_missing'] + u['breadth_present'] == d['window']['sessions'], \
        '两类场次数相加必须等于窗口场数，不然这句声明的分母就是错的'
    if u['breadth_missing']:
        text = next(c['text'] for c in d['caveats'] if c['code'] == 'caliber_break')
        assert '不可比' in text and str(u['breadth_missing']) in text, \
            '跨两类场次却不说"和不可比"，屏幕上就是一句假话'


def test_真库_窗口越短榜越短_且每行场次等于窗口场数():
    try:
        d3 = Q.rank_stability(days=3, limit=50)
        d11 = Q.rank_stability(days=11, limit=50)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f'本机库不可用：{e}')
    if not (d3.get('available') and d11.get('available')):
        pytest.skip('本机库不可读')
    assert len(d11['rows']) <= len(d3['rows']) or d11['total_ranked'] <= d3['total_ranked'], \
        '窗口拉长只会让凑满的票变少，不会变多（榜行数不该反着涨）'
    assert all(r['sessions_used'] == 11 for r in d11['rows']), \
        '进了 11 场榜就必须 11 场每场都有名次 —— 少一场的那个和是少加一项的假优'
    assert d11['total_ranked'] >= len(d11['rows']), '榜行数不能超过进榜总数（截断由 limit 说，不是消失）'
    assert isinstance(d11['caveats'], list) and d11['rule'], '口径句子必须随数交出（页面只转述）'
