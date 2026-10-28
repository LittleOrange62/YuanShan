# -*- coding: utf-8 -*-
"""名次稳步前进榜（近 6 场**名次**一直在往前走的票，xy 两轴合名次 / 总分名次两档可切）。

用户 2026-10-18 点名：「展示这个 xy 名次近 6 个交易日稳步增长的个股，**是名次**！
可以手动切换成总分稳步增长的，可以选择。」

钉住的是六条会被读错、也会被「顺手改宽」的地方：

① **「增长」在这里是名次数字变小**（1 = 全场第一）。所以判据是 `latest < first`，
   方向反了就整屏变成「谁在掉队榜」。
② **只收 xy / oper 两档**（界面上就是这两个按钮）。传 `x`、`score` 之类必须被 `_reject`
   拒掉且不查库 —— 不许悄悄换成 xy 代答（与名次稳定榜那条同一条纪律）。
③ **xy 档要求两个轴各自都净前进**：只看合名次的话，X 轴一次跨 2,000 名可以完全盖住
   Y 轴在同一窗里的恶化，屏幕上却写着「稳步前进」。
④ **凑不满 6 场不进榜**，而且这条写在 SQL 的 `where` 上（`sessions_used = 窗口场数`）：
   挪到页面筛就变成「先取前 N 再筛」，榜会随上限漂。
⑤ **派生列只算一次**：`gain_frac` = 净前进 ÷ 首场名次（排序用的就是它，因为两档尺度不同），
   `steps_total` 必须等于「场次 − 1」，页面一格都不重算。
⑥ **查询失败不许念成「没有稳步前进的票」**；榜被上限截断时必须交出「符合判据共几只」，
   否则 80 行会被读成「全场只有 80 只在稳步前进」。

最后两条**不打桩**：桩绿不代表接线对，真库里的方向、步数与路径必须能自己复算。
"""
import inspect

import pytest

from duanxian import quant as Q

STATS = {'sessions': 6, 'first_day': '2026-10-09', 'last_day': '2026-10-16',
         'universe_min': 5558, 'universe_max': 5563, 'stale_rows': 0,
         'y_levels_min': 41, 'y_levels_max': 41, 'x_levels_min': 5535, 'x_levels_max': 5552,
         'biggest_y_tie': 755}


def _fake_board(*_a, **_k):
    """两行桩数据：一行合名次稳步前进、一行总分稳步前进（同一批行喂两档，钉的是算式不是真序）。"""
    return [
        {'security_code': '601398.SH', 'name': '工商银行', 'board': '主板', 'sessions_used': 6,
         'first_series': 3454, 'latest_series': 181, 'gain_abs': 3273, 'gain_frac': 0.9476,
         'better_steps': 4, 'worse_steps': 1, 'tied_steps': 0, 'biggest_step_gain': 2405,
         'first_x': 3412, 'latest_x': 180, 'first_y': 42, 'latest_y': 1,
         'series_path': '3454→1049→388→998→700→181', 'x_path': '3412→1007→346→956→658→180',
         'y_path': '42→42→42→42→42→1',
         'quadrant': 'Q1', 'oper_value': 31.9374, 'matched_total': 372},
        {'security_code': '601988.SH', 'name': '中国银行', 'board': '主板', 'sessions_used': 6,
         'first_series': 486, 'latest_series': 15, 'gain_abs': 471, 'gain_frac': 0.9691,
         'better_steps': 5, 'worse_steps': 0, 'tied_steps': 0, 'biggest_step_gain': 419,
         'first_x': 18, 'latest_x': 18, 'first_y': 4582, 'latest_y': 906,
         'series_path': '486→300→120→60→30→15', 'x_path': '18→18→18→18→18→18',
         'y_path': '4582→2771→1000→500→200→906',          'quadrant': 'Q2', 'oper_value': 21.5, 'matched_total': 372},
    ]


@pytest.fixture
def stub(monkeypatch):
    calls = {'rows': 0, 'agg': 0}

    def fake_agg(key, sql, params=(), many=False):
        calls['agg'] += 1
        if key.startswith('rank_momentum_stats'):
            return dict(STATS)
        return {'a': '2026-01-27', 'b': '2026-10-16', 'n': 166}

    def fake_rows(sql, params=()):
        calls['rows'] += 1
        return _fake_board()

    def fake_one(sql, params=()):
        calls['rows'] += 1
        return {'matched': 0, 'full_cov': 5555}

    monkeypatch.setattr(Q, '_agg', fake_agg)
    monkeypatch.setattr(Q, '_rows', fake_rows)
    monkeypatch.setattr(Q, '_one', fake_one)
    monkeypatch.setattr(Q, '_latest', lambda table, spec=None: '20261016')
    monkeypatch.setattr(Q, '_labels', lambda: ({'Q1': '双高'}, '测试用标签'))
    monkeypatch.setattr(Q.pg, 'session_has', lambda table, d: True)
    monkeypatch.setattr(Q, 'clear_agg_cache', lambda prefix='': 0)
    return calls


def test_两档各给一份数并交出窗口与步数(stub):
    for m in Q.STEADY_METRICS:
        d = Q.rank_momentum(metric=m, date='2026-10-16')
        assert d['available'], d.get('reason')
        assert d['metric'] == m
        assert d['window']['sessions'] == 6 and d['window']['steps'] == 5
        assert d['window']['is_trading_days'] is True, '「近 6 天」是交易日不是日历日'
        assert d['series_label'] and d['metric_label'], '两档的名字随数据交出，页面不自己取名'


def test_界面没有的那一档被拒且不查库(stub):
    before = stub['rows']
    d = Q.rank_momentum(metric='score', date='2026-10-16')
    assert d['available'] is False
    assert 'xy' in d['reason'] and 'oper' in d['reason'], '要拒就列出能收的那两档'
    assert stub['rows'] == before, '被拒的参数根本不该发查询'
    assert '这一场没有数据' not in d['reason'] and '连不上' not in d['reason']


def test_方向是名次数字变小_not_变大(stub):
    d = Q.rank_momentum(metric='xy', date='2026-10-16')
    assert '越小' in d['universe']['note'] or '变小' in d['rule'], \
        '「名次增长」这句必须当场消歧：前进 = 数字变小'
    src = inspect.getsource(Q.rank_momentum)
    assert 'latest_series < first_series' in src, '方向写反就整屏变成"谁在掉队榜"，而且不会报错'


def test_xy_档要求两个轴各自都净前进(stub):
    """只看合名次会让一轴的跨档掩盖另一轴的恶化 —— 这条只存在于 xy 档，oper 档不该带上。"""
    src = inspect.getsource(Q.rank_momentum)
    assert 'latest_x < first_x and latest_y < first_y' in src
    assert "%s = 'oper' or" in src, '两轴条件要挂在 metric 上：总分档没有"另一轴"可查'
    xy = Q.rank_momentum(metric='xy', date='2026-10-16')
    op = Q.rank_momentum(metric='oper', date='2026-10-16')
    assert xy['thresholds']['require_both_axes'] is True
    assert op['thresholds']['require_both_axes'] is False, '总分档也带这句会被读成"总分要两轴一起算"'


def test_派生列只算一次_页面读的就是这几格(stub):
    d = Q.rank_momentum(metric='xy', date='2026-10-16')
    rows = d['rows']
    assert [r['board_rank'] for r in rows] == [1, 2]
    assert [r['gain_frac'] for r in rows] == [94.8, 96.9], '后端交分数、页面显示百分数：换算只做一次'
    assert all(r['steps_total'] == 5 for r in rows), '前进一步 + 后退 + 持平 必须等于「场次 − 1」'
    assert rows[0]['matched_total'] == 372, '符合判据的总数随行交出，界面才敢说"榜被截断了"'


def test_Y轴档位数是数出来的不是写死的(stub):
    """桩里给 41 档：屏幕上那句必须跟着变成 41。写死 32 的话，回填之后就是一句假话。"""
    d = Q.rank_momentum(metric='xy', date='2026-10-16')
    caveats = {c['code']: c['text'] for c in d['caveats']}
    assert 'y_axis_levels' in caveats
    assert '41' in caveats['y_axis_levels'], '这句必须用本次读数，不能引用注释里的常数'
    assert '755' in caveats['y_axis_levels'], '最大并列档位也要用实测值'
    assert d['universe']['y_levels_min'] == 41


def test_聚合读不回来时不许念成没有稳步前进的票(stub, monkeypatch):
    monkeypatch.setattr(Q, '_agg', lambda key, sql, params=(), many=False: None)
    d = Q.rank_momentum(metric='xy', date='2026-10-16')
    assert d['available'] is False
    assert '不是这些场次没有名次' in d['reason']


def test_榜查询读不回来时同样出声(stub, monkeypatch):
    monkeypatch.setattr(Q, '_rows', lambda sql, params=(): None)
    monkeypatch.setattr(Q, '_agg', lambda key, sql, params=(), many=False: dict(STATS))
    d = Q.rank_momentum(metric='xy', date='2026-10-16')
    assert d['available'] is False and '没读回来' in d['reason']


def test_榜为空时单独数一次进榜母体(stub, monkeypatch):
    """`count(*) over ()` 在零行时一行都交不出来，所以空榜必须另发一发现数 ——
    否则界面只能说"0 只"，读者分不清"没有稳步前进的票"与"这一窗没读全"。"""
    monkeypatch.setattr(Q, '_rows', lambda sql, params=(): [])
    d = Q.rank_momentum(metric='xy', date='2026-10-16')
    assert d['available'] and d['rows'] == []
    assert d['universe']['covered_codes'] == 5555, '凑满 6 场的母体只数要单独交出'
    assert d['total_matched'] == 0
    assert any(c['code'] == 'empty_board' for c in d['caveats'])


def test_截断与空榜是两句话(stub):
    d = Q.rank_momentum(metric='xy', limit=2, date='2026-10-16')
    codes = [c['code'] for c in d['caveats']]
    assert 'board_truncated' in codes and '符合判据的共 372 只' in \
        next(c['text'] for c in d['caveats'] if c['code'] == 'board_truncated')
    assert 'empty_board' not in codes


def test_缓存键名要翻译成人话再上屏(stub):
    """`_agg_label` 那一层负责把内部缓存键翻译成 caveat 文字；漏出
    `rank_momentum_stats:2026-10-16:xy` 就等于把内部标识印在屏幕上。
    直接问翻译函数本身 —— 只查 caveat 会在"这一轮没走缓存"时空过。"""
    label = Q._agg_label('rank_momentum_stats:2026-10-16:xy')
    assert 'rank_momentum_stats:' not in label
    assert '名次稳步前进榜' in label and '2026-10-16' in label and 'X+Y' in label
    assert '总分名次' in Q._agg_label('rank_momentum_stats:2026-10-16:oper')
    d = Q.rank_momentum(metric='xy', date='2026-10-16')
    for c in d['caveats']:
        assert 'rank_momentum_stats:' not in c['text'], c['text']


def test_路径留在同一次聚合里():
    src = inspect.getsource(Q.rank_momentum)
    assert "string_agg(ser::text, '→' order by trade_date)" in src
    assert 'sessions_used = (select count(*) from w)' in src, \
        '凑不满 6 场不进榜这条必须在 SQL 里，挪到页面就变成"先取前 N 再筛"'
    assert 'lag(ser) over' in src, '步数靠窗口函数在同一次扫描里数，别为它再扫一遍表'


def test_端点已接上且参数范围与后端一致():
    # 只读文本不导入：`vr/app.py` 与顶层 `app/` 包抢同一个裸名（见 test_core_logic.py:1746）
    import pathlib
    src = pathlib.Path('vr/app.py').read_text(encoding='utf-8')
    assert '@app.get("/api/quant/rank-momentum")' in src
    assert 'quant.rank_momentum(' in src, '路由写了却不调用实现＝假端点'
    assert "Query('xy', max_length=8)" in src, '参数默认值必须就是后端那两档之一'


# ---------------------------------------------------------------- 不打桩的两条

def test_真库_窗口跨到没有广度分项的那些天就要出声():
    """2026-10-19 回算把名次历史拉长到 2025-08-28，而 `sector_guangdu` 分项
    只存在于 2026-07-29 起的场次 ⇒ 早于那条线的 Y 轴是**少一个分项**算出来的。
    这一屏不许把两类场次当成同一把尺默默排出去。"""
    try:
        old = Q.rank_momentum(metric='xy', limit=10, date='2026-03-16')
        new = Q.rank_momentum(metric='xy', limit=10)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f'本机库不可用：{e}')
    assert old.get('available'), old.get('reason')
    u_old = old['universe']
    assert u_old['breadth_missing'] == old['window']['sessions'], u_old
    assert u_old['breadth_present'] == 0
    codes = {c['code'] for c in old['caveats']}
    assert 'caliber_break' in codes, '整窗都缺广度分项却不吭声，屏幕就在说假话'
    text = next(c['text'] for c in old['caveats'] if c['code'] == 'caliber_break')
    assert 'sector_guangdu' in text and '少一个分项' in text

    # 默认那一屏（最近 6 场）在今天是有广度的 ⇒ 不该冒这条声明；
    # 但这条**不能反过来当判据**：哪天回填把最近几场也洗成缺分项，就该由上面那段现数出来。
    assert new.get('available'), new.get('reason')
    if new['universe']['breadth_missing'] == 0:
        assert 'caliber_break' not in {c['code'] for c in new['caveats']}, \
            '这一窗什么都不缺却挂一条"输入不齐"，声明就变成噪音'
    else:
        assert 'caliber_break' in {c['code'] for c in new['caveats']}


def test_真库_榜必须真的按前进幅度降序_且每行方向自洽():
    try:
        xy = Q.rank_momentum(metric='xy', limit=40)
        op = Q.rank_momentum(metric='oper', limit=40)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f'本机库不可用：{e}')
    for d, tag in ((xy, 'xy'), (op, 'oper')):
        if not d.get('available'):
            pytest.skip(f'本机库这一场不可读（{tag}）：{d.get("reason")}')
        rows = d['rows']
        assert rows, f'真库里近 6 场稳步前进的票不可能 0 只（{tag}）'
        fracs = [r['gain_frac'] for r in rows]
        assert fracs == sorted(fracs, reverse=True), f'{tag} 榜必须按前进幅度从高到低'
        for r in rows:
            assert r['latest_series'] < r['first_series'], f'{r["security_code"]} 方向不对却进了榜'
            assert r['gain_abs'] == r['first_series'] - r['latest_series']
            assert r['better_steps'] >= Q.STEADY_MIN_BETTER
            assert r['worse_steps'] <= Q.STEADY_MAX_WORSE
            assert r['steps_total'] == d['window']['steps'], \
                f'{r["security_code"]} 步数加起来不等于 5 步，说明路径与步数不是同一批行'
            assert r['series_path'].count('→') == d['window']['sessions'] - 1
        assert d['total_matched'] >= len(rows), '榜行数不能超过进榜总数'
    # xy 档另加一条：两轴各自都要净前进（这条是被掩盖风险最高的地方）
    for r in xy['rows']:
        assert r['latest_x'] < r['first_x'] and r['latest_y'] < r['first_y'], \
            f'{r["security_code"]} 有一轴在退却仍进 xy 榜'


def test_真库_定义的键与行上的字段是同一份清单():
    """这一条是「页面不自己写字段含义」的前提：`definitions` 必须正好覆盖行上每一个字段。
    少一个 ⇒ 屏幕上那一格没有出处；多一个 ⇒ 解释对着不存在的字段（两边都不会报错）。"""
    try:
        d = Q.rank_momentum(metric='xy', limit=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f'本机库不可用：{e}')
    if not d.get('available') or not d['rows']:
        pytest.skip(f'本机库不可读：{d.get("reason")}')
    keys, defs = set(d['rows'][0]), set(d['definitions'])
    assert keys - defs == set(), f'这些字段上了屏却没有出处：{sorted(keys - defs)}'
    assert defs - keys == set(), f'这些解释对着不存在的字段：{sorted(defs - keys)}'


def test_真库_两轴合名次就是那两个名次相加():
    """`ser = rx + ry` 是这一屏的定义。读侧接错列（比如把 Y 名次当成综合分名次加进去）
    不会报错，只会让整张榜排序漂走 —— 所以按源表逐场复算榜首那一只。"""
    try:
        d = Q.rank_momentum(metric='xy', limit=10)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f'本机库不可用：{e}')
    if not d.get('available') or not d['rows']:
        pytest.skip(f'本机库不可读：{d.get("reason")}')
    top = d['rows'][0]
    w = d['window']
    check = Q._one("""with wd as (select trade_date from (
                        select distinct trade_date from analytics.score
                         where capability_key = 'oper_score' and rank_in_market is not null
                           and trade_date <= %s order by 1 desc limit 6) x),
                     p as (select s.trade_date,
                                  max(rank_in_market) filter (
                                    where s.capability_key = 'stock_quality') rx,
                                  max(rank_in_market) filter (
                                    where s.capability_key = 'env_resonance') ry
                             from analytics.score s join wd using (trade_date)
                            where s.security_code = %s
                              and s.capability_key in ('stock_quality','env_resonance')
                              and s.rank_in_market is not null
                            group by 1)
                     select sum(rx + ry)::bigint s, count(*)::int n from p""",
                   (w['last_day'], top['security_code']))
    assert check and int(check['n']) == 6, f"{top['security_code']} 近 6 场两轴都该有名次"
    path_sum = sum(int(x) for x in top['series_path'].split('→'))
    assert int(check['s']) == path_sum, \
        f"源表六场合名次之和 {int(check['s'])} 对不上交出的路径 {top['series_path']}"
    assert top['first_series'] == int(top['series_path'].split('→')[0]), '路径首值就是排好序的第一场'
    assert top['latest_series'] == int(top['series_path'].split('→')[-1]), '末值就是最近一场'
