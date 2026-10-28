# -*- coding: utf-8 -*-
"""评分回算这一批的三件闸（用户 2026-10-19：「名次数据库没有的话，就重新写入一下，
并且把数据加工中的代码增加上这一点。其余 119 场你算一下」）。

为什么是这三件，而不是"跑完就完"：

① **名次必须和分数一起写**。库里 166 场只有 47 场带 `rank_in_market`，缺的那 119 场
   一声没出 —— 界面上那两张名次榜因此只能读到最近 47 场，还差点被当成"历史就这么短"。
   所以 `compute_date` 现在先点缺口、缺就整场不落库（顺序是硬要求：先判后写，
   反过来就把坏行交出去了）。
② **覆盖基准前必须留档**。迁入那批 `is_stale=true` 的行是 `--verify` 的对照基准，
   `UPSERT` 按主键原地覆盖、`analytics.score` 主键又没有 `spec_version` ⇒ 覆盖不能并存对账。
   所以日期落进基准窗口就必须带 `--overwrite-legacy`，不带就退 2 且一行都不写。
③ **`--skip-done` 认名次不认行数**。旧行不删（读侧按 `rank_in_market is not null` 自然挡掉），
   所以"这一场写过多少行"永远对不上 —— 按行数判会把已经重跑好的场误判成没算，
   每一趟都白跑四分钟。

最后一条是读侧的：母体只数必须数**有名次的行**。否则 2026-07 那几场回算完，
界面上会报「母体 8,9xx 只」而名次实际只排到 ~5,5xx —— 那句「名次是当日全场第几名」就成假话。
"""
import datetime as dt
import inspect

import pytest

from app.compute import scoring as SC
from ingest.jobs import d2_scores as J


def _row(code='600000.SH', cap='oper_score', rank=1, pct=99.0, quad='Q1'):
    return (code, dt.date(2026, 3, 2), cap, 12.5, rank, pct, quad, None)


# ---------------------------------------------------------------- ① 名次与分数一起写

def test_全有名次时缺口计数为零():
    g = SC.rank_gaps([_row(), _row(code='000001.SZ', rank=2, pct=98.0)])
    assert g == {'ranks_written': 2, 'rank_missing': 0, 'quadrant_null': 0}


def test_名次或分位为空就是缺口而0不算缺():
    assert SC.rank_gaps([_row(rank=None)])['rank_missing'] == 1
    assert SC.rank_gaps([_row(pct=None)])['rank_missing'] == 1, 'percent_rank 也同批必须有值'
    assert SC.rank_gaps([_row(rank=0)])['rank_missing'] == 0, '名次 0 是值不是缺 —— 别把它数成缺口'


def test_象限为空只数oper_score那一行():
    # 其余 capability 的 quadrant 恒为空是设计（`_NOTE['quadrant_design']`），不能记进缺口
    g = SC.rank_gaps([_row(cap='stock_quality', quad=None), _row(quad=None)])
    assert g['quadrant_null'] == 1, g
    assert g['rank_missing'] == 0


def test_缺名次就先报错再写库_顺序反了等于闸不存在():
    src = inspect.getsource(SC.compute_date)
    a = src.index('rank_gaps(score_rows)')
    b = src.index("stat['error'] = (f'{no_rank} 行缺 rank_in_market")
    c = src.index('write_scores(score_rows)')
    assert a < b < c, '必须是：先数缺口 → 有缺口就 return → 才可能走到写库'
    assert 'stat.update(rank_gaps(score_rows))' in src, \
        '台账里要交得出这一场写了几行名次、缺几行 —— 缺口计数必须并进 stat，不是只判不问'
    keys = SC.rank_gaps([_row()])
    assert set(keys) == {'ranks_written', 'rank_missing', 'quadrant_null'}, keys


# ---------------------------------------------------------------- ② 覆盖基准那道门

WIN = (dt.date(2026, 2, 24), dt.date(2026, 7, 27))


def test_基准窗口内的日期被点名_窗口外的一律放行():
    hit = J.legacy_hit([dt.date(2026, 3, 2), dt.date(2026, 7, 28), dt.date(2026, 2, 23)], WIN)
    assert hit == [dt.date(2026, 3, 2)], '边界含两端；外面两天不该拦'
    assert J.legacy_hit([dt.date(2026, 3, 2)], (None, None)) == [], \
        '库里没有迁入基准时不该有门禁 —— 拦了就是拦空气'


def test_没带覆盖旗标就先返回_留档发生在写库之前():
    src = inspect.getsource(J.main)
    refuse = src.index('if not args.overwrite_legacy:')
    snap = src.index('snap = snapshot_legacy()')
    loop = src.index('st = scoring.compute_date(')
    assert refuse < snap < loop, '顺序必须是：先拒 → 才留档 → 才开始算/写'
    assert 'return 2' in src[refuse:refuse + 600], '拒的时候要退 2（backfill_history 认的"要人看"那一档）'


def test_留档是幂等的_而且只复制不删():
    sql = inspect.getsource(J.snapshot_legacy)
    assert 'NOT EXISTS' in sql, '再跑只补没进过基准表的行，不重复堆'
    assert 'DELETE' not in sql.upper(), '旧行不删：读侧按 rank_in_market is not null 自然挡在榜外'
    assert 'WHERE s.is_stale' in sql


# ---------------------------------------------------------------- ③ skip-done 认名次不认行数

def test_skip_done_的SQL里没有按行数判的写法():
    sql = inspect.getsource(J.ranked_dates)
    assert 'NOT is_stale' in sql and 'rank_in_market IS NULL' in sql
    assert "capability_key = 'oper_score'" in sql, '认的是综合分那一行，不是八个 capability 全表'
    assert 'analytics.score_detail' in sql, \
        '做完必须同时有分项明细：只写了一半（分数有、明细空）不能被认成做完'
    src = inspect.getsource(J.main)
    assert 'd not in done' in src and 'return 4' in src, '预算用完要退 4，让下一趟接着补'


def test_真库_分数写了而分项明细没写的那场不能被判成做完():
    """2026-10-19 实测到的半成品：库断在 `write_scores` 与 `write_details` 之间，
    留下 2026-05-21 有 5,500 行带名次的分数、`score_detail` 零行。
    只认名次会把这种场永久跳过，而 `/scores` 的分量解释读不到它且不报错。"""
    try:
        half = J.engine.query("""
            SELECT s.trade_date FROM analytics.score s
             WHERE s.capability_key = 'oper_score' AND NOT s.is_stale
             GROUP BY 1
            HAVING count(*) FILTER (WHERE s.rank_in_market IS NULL) = 0
               AND count(*) > 0
               AND NOT EXISTS (SELECT 1 FROM analytics.score_detail d
                                WHERE d.trade_date = s.trade_date)
             LIMIT 3""")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f'本机库不可用：{e}')
    if not half:
        pytest.skip('本机此刻没有"只写了一半"的场次（说明都被补过了）')
    days = [r['trade_date'] for r in half]
    done = J.ranked_dates(days)
    assert not (set(days) & done), f'这些场只有分数没有分项明细，不该被判成做完：{sorted(set(days) & done)}'


def test_真库_有名次的场才算做完():
    """不打桩：这条要在真库上把"已经带名次的最近场"认成做完，把"整场空名次的场"认成没做完。"""
    try:
        days = [r['trade_date'] for r in J.engine.query(
            "SELECT DISTINCT trade_date FROM analytics.score "
            "WHERE capability_key='oper_score' ORDER BY 1 DESC LIMIT 12")]
    except Exception as e:  # noqa: BLE001
        pytest.skip(f'本机库不可用：{e}')
    done = J.ranked_dates(days)
    assert done, '最近这 12 场里至少有几场是整场带名次的'
    for d in done:
        bad = J.engine.scalar("""
            SELECT count(*) FROM analytics.score
             WHERE capability_key='oper_score' AND trade_date = %s
               AND NOT is_stale AND rank_in_market IS NULL""", (d,))
        assert int(bad or 0) == 0, f'{d} 被判成做完，却还有 {bad} 行缺名次'
    legacy_only = J.engine.query("""
        select trade_date from analytics.score
         where capability_key='oper_score' group by 1
        having count(*) filter (where not is_stale) = 0 limit 1""")
    if legacy_only:
        assert legacy_only[0]['trade_date'] not in done, \
            '一场全是迁入旧行（没有一行是现行判据写的）不能被判成"已回填"'


# ---------------------------------------------------------------- ⑤ 日常那一趟不能被回算档带歪

def test_收盘链那种单日期调用照常走通而且失败会变有声(monkeypatch, capsys):
    """`scores` 在 e1 里是**骨干步**（rc≠0 就地重试、最后把链判红）。
    这一轮给它加了退出码语义：算不出/缺名次 ⇒ rc 3，全成 ⇒ rc 0。
    以前是"打印一行失败然后退 0"，链绿而当天没有评分 —— 那正是要消灭的静默假成功，
    所以这条测试钉的是**新语义**，不是老行为。"""
    import sys
    today = J.engine.query(
        "SELECT max(trade_date) d FROM analytics.score WHERE capability_key='oper_score'"
        "  AND NOT is_stale")[0]['d']
    assert today > WIN[1], '挑的这日必须在迁入基准窗口之外，否则测的是门禁不是日常路径'
    calls = []

    def fake(d, cap_keys=None, dry_run=False, collect=False, task_id=None):
        calls.append(d)
        return {'trade_date': str(d), 'lookback': 72, 'codes': 5500,
                'caps': {'oper_score': {'scored': 5500, 'parts': ['oper_x_v1'],
                                        'coverage': {'oper_x_v1': 5500}}},
                'score_rows': 44000, 'detail_rows': 90000,
                'ranks_written': 44000, 'rank_missing': 0, 'quadrant_null': 0,
                'seconds': 1.0}

    monkeypatch.setattr(SC, 'compute_date', fake)
    monkeypatch.setattr(sys, 'argv', ['d2_scores', '--date', str(today), '--recent', '1'])
    assert J.main() == 0, '全成的一趟必须退 0（骨干步退非 0 会把整条链判红）'
    assert calls == [today], '日常那一趟只算被点名的这一天'
    out = capsys.readouterr().out
    assert '名次 44,000 行（缺 0）' in out, '台账里要看得见这一场写了几行名次'

    def broken(d, cap_keys=None, dry_run=False, collect=False, task_id=None):
        return {'error': '5526 行缺 rank_in_market / percent_rank，这一场不落库'}

    monkeypatch.setattr(SC, 'compute_date', broken)
    monkeypatch.setattr(sys, 'argv', ['d2_scores', '--date', str(today), '--recent', '1'])
    assert J.main() == 3, '缺名次这种失败不能再退 0 —— 那正是"链绿而当天没评分"的来源'


# ---------------------------------------------------------------- ④ 读侧母体口径

def test_名次稳定榜的母体只数有名次的行():
    src = inspect.getsource(__import__('duanxian.quant', fromlist=['rank_stability']).rank_stability)
    per = src[src.index('per as ('):src.index('select count(*)::int sessions')]
    assert 'rank_in_market is not null' in per, \
        '回算历史后同一场会混着迁入旧行；把它们算进母体，屏幕上"名次是当日全场第几名"就是假话'
