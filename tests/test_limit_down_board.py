# -*- coding: utf-8 -*-
"""「跌停分析」那一条读侧：档位尺、两把容差的分叉、以及"筛只筛表、比率说全场"。

用户 2026-10-28 裁定在这屏加一块跌停分析，格式与涨停那块一样（可选交易日 + 连续跌停档位多选）。
跌停侧本机**没有**封单额／首封时刻／编辑归因那三族读数（同花顺揭秘池只交涨停、东财跌停池没采），
所以那一族整列不出现。这里钉的就是这套形状不会写漂：

1. 「连续跌停」= 紧挨着的场次，断一场就归 1（不是几天几跌停那把允许空一场的尺）；
2. 母体那一把容差必须是半分 `LIMIT_EPS`，且比较留在 SQL 侧 `::numeric`
   （numeric 与 Python float 直接比会升成 float8，边界静默翻转 —— 触板那档就栽过一次）；
3. 首页短线情绪那格的「跌停」家数与本屏数出来的是**同一个实现**（不许两条 SQL 各数一遍）；
4. 档位筛选只筛那张表，`dt_count`／`open_rate`／`continuation_rate` 恒定说全场；
5. 库里没有那一天 ⇒ `available:false` 且把"该跑哪一步"交出去，不拿别的日子顶。
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
VR = ROOT / "vr"
if str(VR) not in sys.path:
    # 追加，不插到 0：仓库根有 `app/` 包、vr 里有个 `app.py`，插到 0 会让
    # `from app.compute import …` 拿到 vr/app.py 这个模块而报"不是包"。
    sys.path.append(str(VR))

import market                      # noqa: E402  跌停分析那块在 vr/ 那一层
from duanxian import board_span as bs
from duanxian import limit_down_board as ldb
from duanxian import limit_up_board


def _days(pairs):
    """[('占位', 封没封)] → 带日期的升序序列（规则只看真假与顺序）。"""
    base = dt.date(2026, 9, 1)
    return [(base + dt.timedelta(days=i), ok) for i, (_l, ok) in enumerate(pairs)]


# ---------------------------------------------------------------- 那一把"紧挨着"的尺

def test_连续跌停_只数紧挨着的场次():
    assert bs.streak_from_sealed(_days([(None, True), (None, True), (None, True)])) == 3


def test_中间断一场就归零_这一列与几天几跌停是两把尺():
    """`封 封 空 封`：连板那一列读的是"今天往前紧挨着"，所以是 1；
    同一批序列交给 `span_from_sealed` 会给出 4天3跌停（中间那场空仍算同一轮）。"""
    seq = _days([(None, True), (None, True), (None, False), (None, True)])
    assert bs.streak_from_sealed(seq) == 1
    assert bs.span_from_sealed(seq)[:2] == (4, 3)


def test_今天没封住就是零_不是空值也不是上一轮():
    assert bs.streak_from_sealed(_days([(None, True), (None, True), (None, False)])) == 0
    assert bs.streak_from_sealed([]) == 0


def test_方向不认识就当场抛_不许静默按涨停算():
    """跌停那一屏如果悄悄回落到涨停档位列，屏幕上两块的列名都还在，读的人无法察觉。"""
    with pytest.raises(ValueError):
        bs._side('both')
    assert bs._side('up')[0] == 'limit_up'
    col, expr = bs._side('down')
    assert col == 'limit_down' and '<=' in expr


def test_跌停那一侧的查询走另一列另一个符号_容差仍是参数化的():
    """同一形状、两处列名/符号不同 ⇒ SQL 只能有一份；容差以 `%s::numeric` 进查询，不在 Python 侧比。"""
    seen = {}

    def fake_fetch_df(sql, params=None):
        seen.setdefault('sqls', []).append(sql)
        # 第一发是"6 位码展开成全码"，得给它一行，否则查询根本走不到第二发（那把尺就没人验了）
        if 'reference_data.security' in sql:
            return pd.DataFrame({'security_code': ['000001.SZ'], 'code6': ['000001']})
        return pd.DataFrame([], columns=['code', 'trade_date', 'sealed'])

    import duanxian.pg as pg
    orig_en, orig_fetch = pg.enabled, pg.fetch_df
    pg.enabled, pg.fetch_df = (lambda: True), fake_fetch_df
    try:
        bs._sealed_series(['000001'], '2026-10-27', 5, 'down')
    finally:
        pg.enabled, pg.fetch_df = orig_en, orig_fetch
    sql = seen['sqls'][-1]
    assert 'limit_down is not null' in sql and 'sd.close::numeric <= sd.limit_down::numeric + %s::numeric' in sql
    assert 'limit_up' not in sql, '跌停那把尺的查询里不许出现涨停档位列'


def test_涨停那一侧的容差也带着numeric_两把尺同一个形状():
    """同一条查询模板：`up` 那支也必须是 `%s::numeric`，否则一侧改cast、另一侧留在 float8  promotion。"""
    seen = []

    def fake_fetch_df(sql, params=None):
        seen.append(sql)
        if 'reference_data.security' in sql:
            import pandas as pd
            return pd.DataFrame({'security_code': ['000001.SZ'], 'code6': ['000001']})
        import pandas as pd
        return pd.DataFrame([], columns=['code', 'trade_date', 'sealed'])

    import duanxian.pg as pg
    orig_en, orig_fetch = pg.enabled, pg.fetch_df
    pg.enabled, pg.fetch_df = (lambda: True), fake_fetch_df
    try:
        bs._sealed_series(['000001'], '2026-10-27', 5)      # 默认＝涨停那一侧
    finally:
        pg.enabled, pg.fetch_df = orig_en, orig_fetch
    assert 'sd.close::numeric >= sd.limit_up::numeric - %s::numeric' in seen[-1]


def test_跌停侧的口径文字与列名同步():
    assert '几天几跌停' in bs.rule_note('down')
    assert '几天几板' not in bs.rule_note('down')
    assert bs.rule_note() is bs.RULE_NOTE, '默认还是涨停那一串（现有调用与测试不动）'


def test_格子里那串中文跟着方向换字_跌停不许念成板():
    """label 是屏幕上那一格的值：跌停侧写成「2天2板」就会被读成涨停那一列。"""
    import duanxian.board_span as mod

    seq = {'000001': [(dt.date(2026, 10, 26), True), (dt.date(2026, 10, 27), True)]}
    orig = mod._sealed_series
    mod._sealed_series = lambda codes, day, sessions, direction='up', eps=None: (seq, True)
    try:
        assert mod.boards_by_code(['000001'], '2026-10-27', 'down')['000001']['label'] == '2天2跌停'
        assert mod.boards_by_code(['000001'], '2026-10-27')['000001']['label'] == '2天2板'
    finally:
        mod._sealed_series = orig


# ---------------------------------------------------------------- 跌停家数只有一份实现

def test_跌停家数比较留在SQL侧并带numeric_容差只认那份半分():
    from app.compute.limit_prices import LIMIT_EPS

    seen = {}

    def fake_fetch_df(sql, params=None):
        seen['sql'], seen['params'] = sql, params
        return pd.DataFrame({'n': [28]})

    import duanxian.pg as pg
    orig_en, orig_fetch = pg.enabled, pg.fetch_df
    pg.enabled, pg.fetch_df = (lambda: True), fake_fetch_df
    try:
        assert ldb.sealed_count('2026-10-27') == 28
    finally:
        pg.enabled, pg.fetch_df = orig_en, orig_fetch
    assert 'close::numeric <= limit_down::numeric + %s::numeric' in seen['sql']
    assert 'abs(' not in seen['sql'], 'abs(close-limit)<=eps 那种写法会把 numeric 升成 float8（边界静默翻转）'
    assert seen['params'][1] == LIMIT_EPS


def test_库没连上时交None_不是零():
    """0 只＝"今天没有跌停"；None＝"没读到"。这两种话在界面上完全不同。"""
    import duanxian.pg as pg
    orig = pg.enabled
    pg.enabled = lambda: False
    try:
        assert ldb.sealed_count('2026-10-27') is None
        assert ldb.board_on('2026-10-27')['available'] is False
    finally:
        pg.enabled = orig


def test_首页那一格的跌停家数就是本屏这一份():
    """`limit_up_board.dt_count` 只剩一个转接口：两处各写一条 SQL 迟早给出两个数。"""
    calls = []
    import duanxian.limit_down_board as down

    orig = down.sealed_count
    down.sealed_count = lambda day: (calls.append(day), 11)[1]
    try:
        assert limit_up_board.dt_count('2026-10-27') == 11
    finally:
        down.sealed_count = orig
    assert calls == ['2026-10-27'], 'dt_count 没把动作交给跌停那一份实现（＝又长出第二条 SQL）'


# ---------------------------------------------------------------- 整块读数的诚实性

DAY = '2026-10-27'
ROWS = [
    {'code': '000001', 'name': '甲', 'streak': 4, 'pct': -10.0, 'close': 8.0, 'amount': 1e8,
     'float_cap': 1e9, 'turnover': 12.0, 'limit_down': 8.0},
    {'code': '000002', 'name': '乙', 'streak': 2, 'pct': -10.02, 'close': 9.0, 'amount': 2e8,
     'float_cap': 2e9, 'turnover': 8.0, 'limit_down': 9.0},
    {'code': '000003', 'name': '丙', 'streak': 1, 'pct': -9.98, 'close': 10.0, 'amount': 3e8,
     'float_cap': None, 'turnover': None, 'limit_down': 10.0},
]
BOARD = {'available': True, 'date': DAY, 'dt_count': 3, 'touched_count': 5, 'opened_count': 2,
         'seal_rate': 0.6, 'open_rate': 0.4, 'prev_date': '2026-10-26', 'prev_sealed': 4,
         'streak_ge2_count': 2, 'continuation_rate': 0.5, 'rows': ROWS, 'opened_rows': [],
         'coverage': {'first_day': '2021-08-02', 'last_day': DAY, 'sessions': 1269},
         'caliber': '口径', 'streak_rule': '连续跌停口径', 'streak_warn': '',
         'days_boards_rule': '几天几跌停口径', 'days_boards_warn': '',
         'float_cap_missing': 1, 'no_limit_rows': 2, 'window_sessions': 30, 'eps': 0.005}


@pytest.fixture
def stub_board(monkeypatch):
    import firstboard
    monkeypatch.setattr(firstboard, 'data_session', lambda: (DAY, ''))
    from duanxian import limit_down_board as down
    monkeypatch.setattr(down, 'board_on', lambda day: dict(BOARD))


@pytest.mark.parametrize('tiers,expect', [
    ('', 3), ('2', 1), ('2,3,4p', 2), ('1', 1), ('4p', 1), ('1,2,3,4p', 3),
])
def test_档位只筛表_比率永远说全场(stub_board, tiers, expect):
    d = market._limit_down(DAY, tiers)
    assert len(d['rows']) == expect
    # 这三句说的是整场，跟着筛后的行数走就是假数（本项目反复登记的那族缺陷）
    assert d['dt_count'] == 3 and d['touched_count'] == 5 and d['continuation_rate'] == 0.5
    assert d['tier_counts'] == {'1': 1, '2': 1, '3': 0, '4p': 1}
    assert d['tier_parent_total'] == 3
    assert (d['parent'] == 'tier_filtered') == bool(tiers)


def test_坏档名点名交出去_不静默丢掉(stub_board):
    d = market._limit_down(DAY, '2,9')
    assert len(d['rows']) == 1, '认识的那一档照常筛'
    assert '档位名不认识' in d['caliber_note'] and '9' in d['caliber_note']
    assert '首跌' in d['caliber_note'] or '首跌' in d['tier_labels']['1'], '跌停那侧的中文档名不许借涨停那个"板"字'


def test_筛过之后要在屏上说清母体(stub_board):
    assert '按所选档位筛过' in market._limit_down(DAY, '2')['caliber_note']
    assert '按所选档位筛过' not in market._limit_down(DAY, '')['caliber_note']


def test_缺数那几条声明要跟着出(stub_board):
    """市值缺、limit_down 为空的那几行：话已经在 caliber_note 里，不许被折进说明抽屉后消失。"""
    note = market._limit_down(DAY, '')['caliber_note']
    assert '1 只没有流通市值' in note and '2 行日线没有跌停档位记录' in note


def test_库里没有那一天就说该跑哪一步(monkeypatch):
    import firstboard
    from duanxian import limit_down_board as down
    monkeypatch.setattr(firstboard, 'data_session', lambda: (DAY, ''))
    monkeypatch.setattr(down, 'board_on',
                        lambda day: {'available': False, 'reason': 'stock_daily 里没有这一场'})
    d = market._limit_down('', '')
    assert d['available'] is False and 'stock_daily' in d['reason']
    assert 'rows' in d and d['rows'] == [], '空数组让页面走 available 分支，而不是渲染一只空表'


def test_行序按连续跌停从高到低_同档按跌幅从狠到轻(stub_board):
    rows = market._limit_down(DAY, '')['rows']
    assert [r['code'] for r in rows] == ['000001', '000002', '000003']


def test_连续跌停数不出来的行排最后而不是当1():
    """`or 1` 那种写法会把"没读到"排进首跌档 —— 0/— 与 1 是两种读数。"""
    rows = [{'code': 'A', 'streak': None, 'pct': -10.0}, {'code': 'B', 'streak': 1, 'pct': -10.0}]
    ordered = sorted(rows, key=lambda r: (-(r.get('streak') or 0) if r.get('streak') is not None
                                          else 10 ** 6, r['pct'], str(r.get('code'))))
    assert [r['code'] for r in ordered] == ['B', 'A']


def test_端点走的是同一份实现_and_参数键名():
    """契约钉在源码上：界面发 `date`/`tiers`，FastAPI 丢掉不认识的键＝筛了个寂寞。"""
    src = (ROOT / 'vr' / 'app.py').read_text(encoding='utf-8')
    at = src.index('@app.get("/api/market/limit-down")')
    seg = src[at:src.index('@app.', at + 1)]        # +1：这一段自己就以 @app. 开头
    assert 'date: str = Query' in seg and 'tiers: str = Query' in seg
    assert 'market.get_limit_down(date, tiers)' in seg
    assert '"data"' in seg, '信封全站一律 {"data": ...}，前端 useProbe/get 会剥这一层'
    assert 'raise HTTPException(502' in seg, '判据类端点取数异常要出 502，与涨停那条同一风格'
