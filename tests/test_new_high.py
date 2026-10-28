"""自选股「新高」这一屏的回归（用户 2026-10-07：趋势评分里加一个针对自选分组的新高）。

钉的是六件会骗人的事：

1. **不许叫"历史新高"** —— 本机日线最早 2021-08-02（PG 与通达信 `.day` 同一起点）、
   `reference_data.security.list_date` 全表为空，界面拿到的必须有 `history_from`
   与每行的 `hist_truncated`，否则"新高"这两个字越界；
2. 判定是"目标日复权收盘 ≥ 窗口内最高"，用 `>=`，**没有容差可打折**；
3. 参数不对（window 非法 / 分组不存在）必须说"参数不对"，不许借用"这一场没有数据"那句
   —— 那是两种完全不同的处置（改参数 vs 查回填）；
4. 峰日回捞必须拿**未取整的峰值**做等值匹配：10-07 实测拿取整值匹配，100 只有 58 行配不上，
   页面就成了"峰日读不到"，看着像缺数；
5. 母体分母只数自选清单，**不许在那里发 `exists(...)`**（给 1,093 只票各探一次分区表，
   整块会撞上 60 秒 `statement_timeout`）；"有几只有线"直接用聚合结果的条数；
6. 聚合按小时缓存，**年龄必须随数据交出**，而刚算完的那一次不许被喊成"缓存"。

全部打桩 `pg.fetch_df`：不连库（`conftest.py` 本就拦出站）。返回的形状取自 10-07 真跑一次的结果。
"""

from __future__ import annotations

import pandas as pd
import pytest

from duanxian import pg, quant as Q

AGG = 'with uni as'                      # 主聚合那一句
PEAK = 'peak_close, peak_high) as (values'    # 峰日回捞那一句

# 10-07 实测：复权峰值是 `close × adjustment_factor`，小数位远不止 3 位。
# 页面看的是取整过的 `peak_close_adj`，匹配必须用 `peak_close_exact` 那一份。
EXACT = {'600177.SH': ('8.380012340000', '8.421122330000'),
         '301080.SZ': ('141.0712340000', '143.2209870000'),
         '601398.SH': ('8.280000000000', '8.310000000000')}

ROWS = [
    {'code': '600177.SH', 'name': '雅戈尔', 'board': 'main', 'is_st': False,
     'bars': 1253, 'first_date': '2021-08-02', 'last_date': '2026-09-30',
     'close': 8.38, 'high': 8.42, 'pct_change': 1.20,
     'peak_close_adj': 8.38, 'peak_high_adj': 8.42,
     'peak_close_exact': EXACT['600177.SH'][0], 'peak_high_exact': EXACT['600177.SH'][1],
     'pct_from_peak': 0.0, 'pct_from_high_peak': 0.0,
     'new_high_close': True, 'new_high_intraday': True,
     'traded_on_date': True, 'bars_without_factor': 0},
    {'code': '301080.SZ', 'name': '百普赛斯', 'board': 'chinext', 'is_st': False,
     'bars': 1205, 'first_date': '2022-01-06', 'last_date': '2026-09-30',
     'close': 79.05, 'high': 80.10, 'pct_change': -2.10,
     'peak_close_adj': 141.071, 'peak_high_adj': 143.22,
     'peak_close_exact': EXACT['301080.SZ'][0], 'peak_high_exact': EXACT['301080.SZ'][1],
     'pct_from_peak': -43.96, 'pct_from_high_peak': -44.8,
     'new_high_close': False, 'new_high_intraday': False,
     'traded_on_date': True, 'bars_without_factor': 7},
    # 停牌：目标日没有那一行。它**不是**"没创新高"，是"那天没数"，两种读法必须分得开。
    {'code': '601398.SH', 'name': '工商银行', 'board': 'main', 'is_st': False,
     'bars': 1253, 'first_date': '2021-08-02', 'last_date': '2026-09-29',
     'close': None, 'high': None, 'pct_change': None,
     'peak_close_adj': 8.28, 'peak_high_adj': 8.31,
     'peak_close_exact': EXACT['601398.SH'][0], 'peak_high_exact': EXACT['601398.SH'][1],
     'pct_from_peak': None, 'pct_from_high_peak': None,
     'new_high_close': False, 'new_high_intraday': False,
     'traded_on_date': False, 'bars_without_factor': 0},
]

PEAKS = {'600177.SH': ('2026-09-30', 8.38, '2026-09-30', 8.42),
         '301080.SZ': ('2025-04-18', 91.6, '2025-04-18', 93.1),
         '601398.SH': ('2026-09-29', 8.28, '2026-09-29', 8.31)}


@pytest.fixture(autouse=True)
def _clean_cache():
    """进程内缓存是跨用例的：不清就有"上一用例算好的数被这一用例当成自己的成果"。"""
    for prefix in ('new_high', 'first_session'):
        Q.clear_agg_cache(prefix)
    yield
    for prefix in ('new_high', 'first_session'):
        Q.clear_agg_cache(prefix)


@pytest.fixture
def env(monkeypatch):
    """打桩 `pg.fetch_df`，并把每一次查询的 SQL 与参数记下来供断言。"""
    seen: list[tuple[str, tuple]] = []
    cfg = {'uni_total': 3, 'group_exists': True, 'rows': ROWS, 'peaks': PEAKS}

    def run(sql, params=()):
        seen.append((sql, tuple(params or ())))
        # 判别顺序按"**最具体的先**"：主聚合里内嵌了 `uni` 那段，所以它也含
        # `config.watchlist_group` / `%(gid)s` —— 先判分组存在性就会把聚合认成分组查询。
        if 'min(trade_date)::text' in sql:
            return pd.DataFrame([{'d': '2021-08-02'}])
        if PEAK in sql:
            codes = [str(params[i]) for i in range(0, len(params) - 4, 3)]
            return pd.DataFrame([{'code': c, **dict(zip(
                ['peak_close_date', 'peak_close_raw', 'peak_high_date', 'peak_high_raw'],
                cfg['peaks'].get(c, (None, None, None, None))))} for c in codes])
        if AGG in sql:
            if not cfg['rows']:
                return pd.DataFrame()
            return pd.DataFrame(cfg['rows'])
        if sql.strip().startswith('select count(*)::int n from (select distinct'):
            return pd.DataFrame([{'n': cfg['uni_total']}])
        if 'config.watchlist_group' in sql and 'g.group_id = %(gid)s::int' in sql:
            if not cfg['group_exists']:
                return pd.DataFrame()
            return pd.DataFrame([{'group_id': 705, 'name': '犇犇熊',
                                  'is_focus': False, 'n': 211}])
        if 'config.watchlist_group' in sql:      # 分组清单（给范围下拉用）
            return pd.DataFrame([{'group_id': 705, 'name': '犇犇熊',
                                  'is_focus': False, 'n': 211}])
        return pd.DataFrame([{'ok': 1}])         # `_alive()` 那句 select 1

    monkeypatch.setattr(pg, 'enabled', lambda: True)
    monkeypatch.setattr(Q.pg, 'enabled', lambda: True)
    monkeypatch.setattr(Q.pg, 'latest_session', lambda table: '2026-09-30')
    monkeypatch.setattr(Q.pg, 'fetch_df', run)
    return {'seen': seen, 'cfg': cfg}


def _agg_calls(seen):
    return [s for s, _ in seen if AGG in s and PEAK not in s]


# ------------------------------------------------------------------ 名字与口径

def test_必须交出历史起点与每行的截断标记(env):
    """这是"不许叫历史新高"那条的数据支撑：界面只转述后端交出的这两个字段。"""
    d = Q.new_high(limit=10)
    assert d['available'] is True
    assert d['history_from'] == '2021-08-02'
    assert '库内新高' in d['caliber'] and '不是上市以来新高' in d['caliber']
    by = {r['code']: r for r in d['rows']}
    assert by['600177.SH']['hist_truncated'] is True      # 第一根就贴着库起点
    assert by['301080.SZ']['hist_truncated'] is False     # 2022 年才有 → 全史在库


def test_新高判定是复权收盘不小于窗口最高_没有容差可打折(env):
    by = {r['code']: r for r in Q.new_high(limit=10)['rows']}
    assert by['600177.SH']['new_high_close'] is True
    assert by['600177.SH']['pct_from_peak'] == 0.0
    assert by['301080.SZ']['new_high_close'] is False
    assert by['301080.SZ']['pct_from_peak'] == pytest.approx(-43.96)


# ------------------------------------------------------------------ 参数不对 vs 没数

def test_窗口非法要说参数不对_不许借用这一场没有数据那句(env):
    """10-07 实测到过：`window=999` 被回成"2026-09-30 这一场没有数据"，
    看的人会去查回填，而真正要改的是请求参数。"""
    d = Q.new_high(window='999')
    assert d['available'] is False
    assert 'window 只接受' in d['reason']
    assert '这一场没有数据' not in d['reason']


def test_分组不存在也说参数不对(env):
    env['cfg']['group_exists'] = False
    d = Q.new_high(group_id=999999, limit=10)
    assert d['available'] is False and '没有 group_id=999999' in d['reason']
    assert '这一场没有数据' not in d['reason']


def test_自选清单为空时不许给出母体条数(env):
    env['cfg']['uni_total'] = 0
    d = Q.new_high(limit=10)
    assert d['available'] is False and '自选清单是空的' in d['reason']
    assert _agg_calls(env['seen']) == [], "清单都空了就不该再去扫全库聚合"


# ------------------------------------------------------------------ 取数结构

def test_峰日回捞拿的是未取整的峰值(env):
    """主聚合同时交出两份：页面看的 `peak_close_adj`（取整过）与匹配用的
    `peak_close_exact`（库里原值）。用前者做等值匹配，实测 100 只有 58 行配不上。"""
    d = Q.new_high(limit=10)
    peak = [(s, p) for s, p in env['seen'] if PEAK in s]
    assert peak, '峰日回捞那一句没发出去'
    sent = [str(x) for x in peak[0][1]]
    for code, (pc, ph) in EXACT.items():
        assert pc in sent and ph in sent, f'{code} 的未取整峰值没传进去'
        assert '8.38' not in sent or pc in sent
    by = {r['code']: r for r in d['rows']}
    assert by['600177.SH']['peak_close_date'] == '2026-09-30'
    assert by['301080.SZ']['peak_close_date'] == '2025-04-18'
    assert by['301080.SZ']['peak_close_raw'] == 91.6


def test_母体计数里不许出现_exists探测(env):
    """第 5 条：给 1,093 只票各发一次分区表探测会把整块推到 60 秒超时之外。"""
    Q.new_high(limit=10)
    assert not [s for s, _ in env['seen'] if 'filter (where exists' in s]


def test_有几只票有线用聚合条数_没线的照实交差(env):
    d = Q.new_high(limit=10)
    assert d['universe_total'] == 3
    assert d['universe_with_data'] == 3 and d['rows_total'] == 3
    assert d['universe_without_data'] == 0
    row = next(r for r in d['rows'] if r['code'] == '601398.SH')
    assert row['traded_on_date'] is False and row['close'] is None


def test_窗口与分组都进了缓存键_不会串成同一份数(env):
    """两个入口（全部自选 / 某个组、all / 250 天）若命中同一份缓存，页面会把
    一个范围的排行端给另一个范围 —— 数字各自都对，拼起来是错的。"""
    Q.new_high(limit=10)
    Q.new_high(limit=10, window='250')
    Q.new_high(limit=10, group_id=705)
    assert len(_agg_calls(env['seen'])) == 3, '三种范围/窗口应当各算一次'


def test_缓存年龄交出且刚算完那次不喊缓存(env):
    d1 = Q.new_high(limit=10)
    assert d1['cache_age_seconds'] is not None and d1['cache_age_seconds'] <= 1.0
    assert not any(c['code'] == 'cached_aggregates' for c in d1['caveats']), \
        '刚在同一个请求里算出来的数不许被说成缓存值'
    d2 = Q.new_high(limit=10)
    assert len(_agg_calls(env['seen'])) == 1, '第二次打开应当命中缓存'
    # 两次调用只差几毫秒，那一小时前的"实算时刻"还没到 1 秒，所以这时候**不该**喊缓存
    # （`_cache_caveat` 就是这么定的：对新鲜数喊"这是缓存"反而是误导）。
    assert not any(c['code'] == 'cached_aggregates' for c in d2['caveats'])


def test_缓存过了一秒就必须自报年龄(env):
    """同一条规矩的另一半：条目真的老了（>1 秒）时，年龄必须出现在返回值与 caveat 里，
    否则刚回填完场次的人会以为看的是此刻的库。"""
    d1 = Q.new_high(limit=10)
    key = [k for k in Q._AGG if k.startswith('new_high:')]
    assert key, '聚合结果没进缓存'
    stamp, value = Q._AGG[key[0]]
    Q._AGG[key[0]] = (stamp - 45, value)          # 假装是一分钟前实算的
    d2 = Q.new_high(limit=10)
    assert d2['cache_age_seconds'] >= 44
    assert any(c['code'] == 'cached_aggregates' and '4' in c['text'] for c in d2['caveats'])
    assert len(_agg_calls(env['seen'])) == 1, '老了但仍在一小时内 —— 不该重扫'


def test_重新取数会把聚合与峰日两份一起丢掉(env):
    """只清一份的后果：新排行配上旧峰日。两个键共用 `new_high` 前缀，一起清才干净。"""
    Q.new_high(limit=10)
    agg_before = len(_agg_calls(env['seen']))
    peak_before = len([s for s, _ in env['seen'] if PEAK in s])
    Q.new_high(limit=10, refresh=True)
    assert len(_agg_calls(env['seen'])) == agg_before + 1
    assert len([s for s, _ in env['seen'] if PEAK in s]) == peak_before + 1


def test_连不上库时按读不到处理不折成空表(env, monkeypatch):
    monkeypatch.setattr(Q.pg, 'fetch_df', lambda sql, params=(): None)
    d = Q.new_high(limit=10)
    assert d['available'] is False
    assert '自选股新高' in d['reason']
