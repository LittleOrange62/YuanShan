# -*- coding: utf-8 -*-
"""名次公用组件（`duanxian/ranking.py`）的回归：**名次这件事只准有一处实现**。

2026-10-19 第二稿背景下这条纪律更要说清：加权分换成「次数×分值」后没有任何归一化，
榜与「达标后逐日走势」的母体都改成按**当日名次**取（前 N 名，同分并列整批进来），
名次算错的后果从"看着别扭"变成"母体本身就取错人"。用户定的规则（原话摘要）：

* 同分同名、下一名**按位置顺延**：[10,10,10,9.9,9,9,8,None] ⇒ [1,1,1,4,5,5,7,None]
  （standard competition ranking，SQL 的 `rank()`），**不是** dense（1,1,1,2,3…）；
* NULL／非数字不参与排名、**不占号**（内存侧交 `None`；SQL 侧 `nulls last` 还得配
  `where <score> is not null` 的母体，光 nulls last 它照样占一个号）；
* 分母（universe）只数**有分的**行；
* 界面上那句解释只住 `RULE_NOTE` 一份，payload 里 `rank.rule` 必须就是那个对象本身。

生产 docstring（`quant._energy_rank_expr`）写着"与 `rank_sql`/`rank_in_memory` 是同一条规则，
`tests/test_ranking_shared.py` 逐位比过" —— 这份文件就是那句承诺的本体。
"""
import pytest

from duanxian import pg, quant
from duanxian import ranking
from app.compute import volume_energy as venc

# 用户那句话的原始数据与期望值（写死，不许用推导生成 —— 推导错了这里就跟着错）
USER_SCORES = [10, 10, 10, 9.9, 9, 9, 8, None]
USER_RANKS = [1, 1, 1, 4, 5, 5, 7, None]

# 对表用的 10 行假数据：两组并列（10×3、9×2）+ 一个 None
PARITY_SCORES = [12.5, 10.0, 10.0, 10.0, 9.9, 9.0, 9.0, 8.0, 7.5, None]
PARITY_RANKS = [1, 2, 2, 2, 5, 6, 6, 8, 9, None]


def _values_clause(scores):
    """把假分数拼成 `t(idx, score)` 的 (VALUES ...)，全部显式 cast（NULL 不 cast 会被推成 text）。"""
    return ', '.join(f"({i}, {'NULL' if s is None else float(s)}::numeric)"
                     for i, s in enumerate(scores))


def _sql_ranks(expr, scores, filter_not_null=True):
    """在真库对 `expr`（rank_sql/dense_rank_sql 的产物）跑 (VALUES ...)，交回 {idx: 名次}。"""
    df = pg.fetch_df(f"""select idx, {expr} as r
                           from (values {_values_clause(scores)}) as t(idx, score)
                          {'where score is not null' if filter_not_null else ''}
                          order by idx""", ())
    assert df is not None, '生产片段在真库上跑不通'
    return {int(r['idx']): int(r['r']) for r in df.to_dict('records')}


# ------------------------------------------------------------------ ① 用户那句话（内存侧，写死期望值）

def test_用户那句话写死_同分同名下一名按位置顺延_不是稠密递增():
    # 2026-10-19 第二稿：次数×分值，无归一化 ⇒ 同分是常态（一天顶档＝1 分），顺延语义必须钉死
    rows = [{'score': s} for s in USER_SCORES]
    got = [r['rank'] for r in ranking.rank_in_memory(rows)]
    assert got == USER_RANKS, \
        f'期望 {USER_RANKS}，实得 {got} —— 10 的下一个是第 4（不是第 2），9 的下一个是第 7'
    # 并列组内部同名、组间按**已排进来的只数**顺延（dense 化＝第 4 名变第 2 名，用户点名否掉的那种）
    assert got[3] == 4 and got[6] == 7
    # rank_in_memory 交回新列表，不许把 rank 塞回调用方原 dict（两处各改一份＝第二份判据）
    assert 'rank' not in rows[0]


# ------------------------------------------------------------------ ② 内存与 SQL 逐位对表

def test_rank_in_memory与SQL的rank_over在同批假数据上逐位一致():
    """Python 侧与 SQL 侧是同一件事的两种写法——对不上就是有两处实现各漂各的。"""
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库跑 rank_sql 片段')
    mine = [r['rank'] for r in ranking.rank_in_memory([{'score': s} for s in PARITY_SCORES])]
    assert mine == PARITY_RANKS, '先自证内存侧与写死的期望一致'
    sql_r = _sql_ranks(ranking.rank_sql('score desc nulls last'), PARITY_SCORES)
    # 逐位：有分的每一位名次相同
    for i, s in enumerate(PARITY_SCORES):
        if s is not None:
            assert sql_r[i] == mine[i], f'第 {i} 位（分 {s}）：SQL {sql_r[i]} ≠ 内存 {mine[i]}'
    assert len(sql_r) == sum(1 for s in PARITY_SCORES if s is not None)
    # NULL 在 SQL 里光靠 `nulls last` **照样占号**（排完序被编成最后一名）——
    # 所以母体必须配 `where score is not null`（`quant` 两条母体 SQL 都这么写），
    # 内存侧才交 rank=None。这一句钉的是"为什么不筛就会多出一个假名次"。
    unfiltered = _sql_ranks(ranking.rank_sql('score desc nulls last'), PARITY_SCORES,
                            filter_not_null=False)
    n_scored = sum(1 for s in PARITY_SCORES if s is not None)
    assert unfiltered[len(PARITY_SCORES) - 1] == n_scored + 1, \
        'NULL 不筛掉就会被编成"最后一名"—— 这正是母体要 where is not null 的原因'


# ------------------------------------------------------------------ ③ rank 与 dense_rank 是两种东西

def test_dense_rank用在榜上会红_并列后跳号与不跳号必须分得开():
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库跑两段片段')
    competition = _sql_ranks(ranking.rank_sql('score desc nulls last'), USER_SCORES)
    dense = _sql_ranks(ranking.dense_rank_sql('score desc nulls last'), USER_SCORES)
    assert [competition[i] for i in range(7)] == [1, 1, 1, 4, 5, 5, 7], \
        'rank()：并列 3 只第 1，下一个是第 4（顺延）'
    assert [dense[i] for i in range(7)] == [1, 1, 1, 2, 3, 3, 4], \
        'dense_rank()：并列后不跳号 —— 这是"第几档"的口径，不是名次'
    assert competition != dense, '两个函数给出一样的数＝片段拼接写坏了，公用组件的区分承诺失效'
    # 防误用：榜单要是拿 dense_rank 出名次，"9.9 分那只"会从第 4 名被念成第 2 名。
    assert dense[3] == 2 and competition[3] == 4, \
        '这条差值就是"用错一个函数，第 4 名变第 2 名"的最小复现'


# ------------------------------------------------------------------ ④ NULL／非数字不占号 + 真库分母

def test_内存侧NULL与非数字NaN交回None且不占号():
    rows = [{'score': v} for v in (5.0, None, 'abc', float('nan'), 4.0)]
    got = [r['rank'] for r in ranking.rank_in_memory(rows)]
    assert got == [1, None, None, None, 2], \
        f'缺数/坏数必须整排 None（不占号）：实得 {got} —— 把它们当 0 分参与排名＝把"没数据"念成"最差"'
    scored = sorted(r for r in got if r is not None)
    assert scored == [1, 2], '有分的那批号数连续无洞：None 若占了号，这里会冒出 3'


def test_真库母体只数有分的行_universe与现算逐位对上():
    """`rank.universe` 是分母承诺：scored（有分只数）与 rows_total（含 NULL 的行数）
    是两个数，界面念"第 40 名（共 N 只有分）"用的是前者。差值实测现算，不背常量。"""
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    d, fp = '2026-10-19', venc.fingerprint()
    live = pg.fetch_df("""select count(dbl_energy)::int as scored, count(*)::int as rows_total,
                                count(*) filter (where dbl_energy is null)::int as unscored,
                                max(dbl_energy)::float as top
                           from analytics.volume_double
                          where trade_date = %s and energy_fp = %s""", (d, fp))
    assert live is not None and len(live) == 1
    row = live.to_dict('records')[0]
    if not row['rows_total']:
        pytest.skip(f'库里 {d} 这一批（{fp}）没有行，等回填完再跑')
    assert row['scored'] != row['rows_total'], \
        '这一场必有没加权分的行（日线里没行情的票交 NULL）—— 两个数相等说明 NULL 被折进了分母'
    assert row['unscored'] == row['rows_total'] - row['scored'] > 0

    out = quant.volume_double(date=d, limit=1)
    if not out.get('available'):
        pytest.skip(f'{d} 读不到榜单：{out.get("reason")}')
    universe = out['rank']['universe']
    assert universe is not None, '聚合失败了也会交 null —— 但真库这条不该 null'
    assert universe['scored'] == row['scored'], '体里的分母必须与现算逐位对上（差值几只是事实，不钉数）'
    assert universe['rows_total'] == row['rows_total']
    assert universe['unscored'] == row['unscored']
    assert universe['top_score'] == pytest.approx(row['top'], abs=1e-4), \
        '榜首分交回的是同一批的 max，不是别的批次'


# ------------------------------------------------------------------ ⑤ tie_counts

def test_tie_counts与手算一致_none不进字典():
    rows = ranking.rank_in_memory([{'score': s} for s in PARITY_SCORES])
    # 手算：第 1 名 1 只、第 2 名并列 3 只、第 5 名 1 只、第 6 名并列 2 只、第 8/9 名各 1 只；None 不算
    assert ranking.tie_counts(rows) == {1: 1, 2: 3, 5: 1, 6: 2, 8: 1, 9: 1}
    assert max(ranking.tie_counts(rows)) == 9 and sum(ranking.tie_counts(rows).values()) == 9, \
        '并列只数加满＝有分的只数；None 行不进字典（界面"第 1（并列 3 只）"读的就是这份）'


# ------------------------------------------------------------------ ⑥ 文案只有一份 + 生产实现互证

@pytest.fixture
def stubbed(monkeypatch):
    """给 `volume_double` 的最小桩（不碰真库）：三路聚合各自分流，捕获主 SQL 与体。"""
    captured = {}

    def _rows(sql, params=()):
        if 'count(dbl_energy)::int as scored' in sql:      # 名次分母（universe）聚合
            captured['uni_sql'] = sql
            captured['uni_params'] = params
            return captured.get('uni_rows')
        if 'count(*) as n' in sql:                          # 象限全场计数
            return list(captured.get('counts') or [])
        if 'as hit' in sql:                                 # 「稳步提升」全场分布
            return captured.get('step_pool')
        captured['sql'] = sql
        captured['params'] = params
        return captured['rows']

    monkeypatch.setattr(pg, 'enabled', lambda: True)
    monkeypatch.setattr(quant, '_alive', lambda: True)
    monkeypatch.setattr(quant.pg, 'session_has', lambda table, d: True)
    monkeypatch.setattr(quant, '_latest', lambda table: '2026-10-19')
    monkeypatch.setattr(quant, '_rows', _rows)
    monkeypatch.setattr(quant, '_agg',
                        lambda key, sql, params=(), many=False: (_rows(sql, params) or [None])[0])
    monkeypatch.setattr(quant, '_labels', lambda: ({'Q1': '进攻', 'Q2': '持有不加仓',
                                                    'Q3': '试探', 'Q4': '回避'}, '测试'))
    monkeypatch.setattr(quant, '_coverage', lambda tables: {})
    monkeypatch.setattr(quant, '_comments', lambda table: {})
    monkeypatch.setattr(quant, '_cache_caveat', lambda keys: [])
    monkeypatch.setattr(venc, 'status', lambda: {'in_sync': True, 'profile_source': '测试',
                                                 'batches': [], 'distribution': {},
                                                 'scale': venc.scale()})
    monkeypatch.setattr(quant.pg_sources, 'concept_rollup',
                        lambda codes: {'pool_size': 1, 'covered': 1, 'rows': []})
    monkeypatch.setattr(quant.day_returns, 'returns_by_code', lambda codes, d, span=None: {})
    captured['rows'] = [{'code': '600000.SH', 'name': '测试股', 'board': '主板', 'market': 'SH',
                         'win_days': 40, 'bars': 40, 'ma60_days': 40,
                         'dbl_energy': 1.25, 'dbl_count': 9, 'dbl_prev_count': 2,
                         'cross_count': 5, 'dev_med': 1.2, 'dev_max': 4.0,
                         'above_ratio': 55.0, 'ma60_chg': 0.3, 'avg_amt_yuan': 1.2e8,
                         'close_last': 7.7, 'ma60_last': 7.6, 'dbl_step_gap': 2.0,
                         'dbl_step_gap_null_reason': None, 'quadrant': 'Q2',
                         'oper_value': -5.1, 'x_quality': 14.2, 'y_env': -18.0,
                         'rank_in_market': 1200, 'percent_rank': 78.4,
                         'rank_in_day': 7, 'is_stale': False}]
    captured['uni_rows'] = [{'scored': 5514, 'rows_total': 5527,
                             'top_score': 11.61, 'unscored': 13}]
    return captured


def test_名次文案只有一份_body里的rank_rule就是公用组件那个对象(stubbed):
    """界面/组件不许另写一句名次解释 —— `quant` 侧连"抄一份字符串"都不许，
    交回的必须**是 `ranking.RULE_NOTE` 那个对象本身**（改一处，全站跟着变）。"""
    assert quant.RANKING is ranking, 'quant 必须引同一个模块对象，不许自备第二份名次实现'
    out = quant.volume_double()
    assert out['available'] is True
    assert out['rank']['rule'] is ranking.RULE_NOTE
    assert out['rank']['rule'] == ranking.RULE_NOTE    # 同对象蕴含逐字相等，两条都钉住防"复制后各自改"
    assert out['rank']['rule_short'] is ranking.RULE_NOTE_SHORT
    assert out['rank']['by'] == 'dbl_energy', '名次按加权分排（第二稿），不是 dbl_count'
    assert '顺延' in out['rank']['rule'] and '不占号' in out['rank']['rule'], \
        '那份文案必须自己把两条边界说全：下一名顺延、没得分的不占号'
    # 分母如实透传：桩给什么体里就是什么（界面拿 scored 念"共 N 只有分"，不许 body 自己再数一遍）
    assert out['rank']['universe'] == {'scored': 5514, 'rows_total': 5527,
                                       'top_score': 11.61, 'unscored': 13}
    # 主查询的名次列走相关子查询（全场名次），不许 rank() over() 被筛参一截就变"这堆里的第 1"
    assert 'end as rank_in_day' in stubbed['sql'] and 'x.dbl_energy > v.dbl_energy' in stubbed['sql']
    assert 'rank() over' not in stubbed['sql'], \
        '榜上的 rank_in_day 不许用窗口函数算 —— where 之后 rank() 会把"全场名次"筛成"这堆第 1"'


def test_榜上相关子查询的名次与rank_sql当日逐位一致():
    """`quant._energy_rank_expr`（1＋同场同批分更高的只数）与公用组件 `rank_sql`
    是同一条规则 —— 生产注释承诺过"逐位比过"，这里兑现。真库现算，只比数、不背数。"""
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    d, fp = '2026-10-19', venc.fingerprint()
    df = pg.fetch_df(f"""select r.code, r.expr_rank, r.win_rank
                           from (select v.security_code as code,
                                        {quant._energy_rank_expr('v')} as expr_rank,
                                        {ranking.rank_sql('v.dbl_energy desc nulls last')} as win_rank
                                   from analytics.volume_double v
                                  where v.trade_date = %s and v.energy_fp = %s
                                    and v.dbl_energy is not null
                                  order by v.dbl_energy desc
                                  limit 300) r""", (d, fp))
    assert df is not None and len(df) > 0, f'{d} 这一批（{fp}）没行，等回填完再跑'
    bad = df[df['expr_rank'] != df['win_rank']]
    assert bad.empty, f'两种写法对不上（名次算法出现第二处实现）：\n{bad.head(8)}'


def test_档案那块的名次与倍量榜那个名次是同一个数():
    """2026-10-19 之前档案那块自己 `rank() over (order by … , security_code)` 破并列，
    于是同一个「当日全场名次」在倍量榜与个股档案上是两个数（看数字看不出对错）。
    现在两处必须逐只一致，且那句解释必须是公用组件那一份对象（真库现算，只比数、不背数）。"""
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    from duanxian import stock_dossier as SD
    d = quant._latest('analytics.volume_double')
    fp = venc.fingerprint()
    df = pg.fetch_df(f"""select v.security_code as code,
                               {quant._energy_rank_expr('v')} as board_rank
                          from analytics.volume_double v
                         where v.trade_date = %s and v.energy_fp = %s
                           and v.dbl_energy is not null
                         order by v.dbl_energy desc limit 6""", (d, fp))
    assert df is not None and len(df) == 6, f'{d} 这一批（{fp}）取不到 6 只有分的票'
    for r in df.to_dict('records'):
        blk = SD._blk_volume_double(str(r['code']), d)
        assert blk['available'] is True, blk
        assert blk['rank_in_market'] == int(r['board_rank']), \
            f"{r['code']}：倍量榜第 {r['board_rank']} 名，档案却念 {blk['rank_in_market']}"
        assert blk['rank_rule'] is ranking.RULE_NOTE_SHORT, '档案那句名次解释必须是公用组件那一份'
    null_df = pg.fetch_df("""select security_code as code from analytics.volume_double
                              where trade_date = %s and energy_fp = %s
                                and dbl_energy is null limit 1""", (d, fp))
    rows = (null_df.to_dict('records') if null_df is not None else [])
    if rows:
        blk = SD._blk_volume_double(str(rows[0]['code']), d)
        assert blk['rank_in_market'] is None, \
            '没有加权分的票在档案里也不许占一个号（那是"编一个名次"）'
