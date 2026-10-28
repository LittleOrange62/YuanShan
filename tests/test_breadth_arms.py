# -*- coding: utf-8 -*-
"""阿姆氏（`analytics.market_breadth` 的 `up_amount` / `down_amount` / `arms_ratio`）的闸。

用户 2026-10-20 夜点名的两个每日入库指标里的第二个：「上涨的这些票子，它的成交额
除以下跌票子的成交额」。这一族只有三件事值得写测试，其余都是 SQL 的既成事实：

1. **算不出必须 NULL，绝不写 0、绝不写 Infinity**。0 在这里是一个真实读数
   （"下跌票一分钱没成交"＝极度强势），NULL 才是"这一天判不出来"。混在一起，
   界面上那条折线会在判不出的那天画出一根假的高点。
2. **分子分母与 `up_count` / `down_count` 是同一批票、同一个判据**。另起一套母体
   （比如忘了排除 `pct_change IS NULL`），"家数"和"钱"就说的不是同一天，
   阿姆斯比值与涨跌家数会互相打脸，而这两项是并排放在首页看的。
3. **库里的数必须能被日线原始行复算出来**（逐位，不是约等）。

前两条是纯 SQL 片段 / 源码级（不打桩、不碰库也能跑）；后两条要真库，
`pg.enabled()` 不通才 skip —— 本机库是 financetool，跑通是常态。

写法参照 `tests/test_energy_track.py`、`tests/test_momentum_follow.py` 的真库那一节。
"""
from __future__ import annotations

import pathlib
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext

import pytest

from duanxian import pg

REPO = pathlib.Path(__file__).resolve().parent.parent


def _flat(s: str) -> str:
    """把 SQL/源码压成单空格一行 —— 断言判"是不是同一个式子"，不判它怎么换行。"""
    return re.sub(r'\s+', ' ', s)


# ---------------------------------------------------------------- ① 比值片段：NULL 语义

def test_比值片段_分母为0或任一边缺都是NULL():
    """`round(u / nullif(d, 0), 6)` 这一式的语义，在真 PG 上逐格验。

    为什么要在库里跑而不是纯 Python：`nullif` 不是装饰 —— PostgreSQL 里 numeric
    **除以 0 是直接报错**（不是 inf），没有 `nullif` 整条回填会在"下跌 0 家"那天当场炸；
    而 `u` 或 `d` 为 NULL 时结果是 NULL，这正是"任一边缺 ⇒ 判不出"要的落库形状。
    """
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    df = pg.fetch_df("""
        SELECT v.tag, round(v.u / nullif(v.d, 0), 6)::text AS r
          FROM (VALUES ('分母0', 100.0, 0.0),
                       ('分母缺', 100.0, NULL),
                       ('分子缺', NULL, 50.0),
                       ('两边都有', 100.0, 80.0)) AS v(tag, u, d)
    """)
    assert df is not None and len(df) == 4, df
    got = dict(zip(list(df['tag']), list(df['r'])))
    NULLISH = ('None', 'nan', 'NaN', 'NaT')
    for tag in ('分母0', '分母缺', '分子缺'):
        assert str(got[tag]) in NULLISH, f'{tag} 必须落成 NULL，得到 {got[tag]!r}'
    assert got['两边都有'] == '1.250000', f'两边都有时必须落地，得到 {got["两边都有"]!r}'
    assert not any(str(x) in ('Infinity', '-Infinity', 'inf') for x in got.values()), got


def test_写侧比值式每一处除以下跌额都带NULLIF兜分母():
    """`app/compute/breadth.py` 里每一个"÷ 下跌额"都必须是 `/ NULLIF(<下跌额>, 0)`。

    每日链（`refresh` 的 upsert）与全史回算（`backfill`）是两条路，两条都得兜；
    漏一条的后果不是报错而是**静默写 0**（写成 `coalesce(..., 0)` 那种兜法）——
    界面会在判不出的那天画出一个"下跌侧彻底没量"的假信号。
    """
    src = _flat((REPO / 'app' / 'compute' / 'breadth.py').read_text(encoding='utf-8'))
    # 只看 SQL 里那些带别名前缀的除法式（`EXPLAIN` 里那句人话 `up_amount / down_amount` 不算）
    divs = re.findall(r'\w+\.up_amount\s*/\s*[^;]{0,40}', src)
    # 两条路都要有：每日链（`refresh` 的 upsert）与全史回算（`backfill`）
    assert len(divs) >= 2, f'只找到 {len(divs)} 处带别名的比值除法式，链路与回算有一条不见了'
    assert divs, '写侧找不到阿姆斯的除法式 —— 口径被改动，请同步这条测试与列注释'
    for seg in divs:
        assert 'NULLIF' in seg.upper(), f'除以下跌额却没有 nullif 兜分母：/ {seg}'
    assert 'coalesce(c.down_amount' not in src.lower(), '分母被 coalesce 成 0 ＝ 判不出写成 0'
    assert 'coalesce(s.down_amount' not in src.lower(), '同上（回算那条）'


# ---------------------------------------------------------------- ② 同一批票、同一判据

def test_涨跌成交额与涨跌家数同判据同母体():
    """`up_count` / `up_amount` 必须用同一个别名、同一个 FILTER 判据。

    这是这条链最容易漂的地方：家数在 `today` CTE（已排除 `pct_change IS NULL`）上算，
    成交额一旦另起一个子查询（忘了那个排除、或多筛了 `amount_yuan > 0`），
    两个数数的就不是同一批票，比值与家数会互相打脸。
    """
    from app.compute import breadth

    core = _flat(breadth.CORE_SQL)
    # `today` 这个母体的两个条件，一个都不能少
    assert re.search(r'today AS \( SELECT .*? FROM market_data\.stock_daily '
                     r'WHERE trade_date = %\(d\)s AND pct_change IS NOT NULL \)', core), core[:400]
    assert 'amount_yuan' in core.split('today AS (')[1].split(')')[0], \
        '成交额没进 today CTE ＝ 它在别的母体上算'

    for sign in ('>', '<'):
        cnt = re.search(r'count\(\*\) FILTER \(WHERE (\w+)\.pct_change %s 0\)' % sign, core)
        amt = re.search(r'sum\((\w+)\.amount_yuan\) FILTER \(WHERE (\w+)\.pct_change %s 0\)' % sign,
                        core)
        assert cnt and amt, f'pct_change {sign} 0 的家数或成交额式子在 CORE_SQL 里找不到'
        assert cnt.group(1) == amt.group(2) == amt.group(1), \
            f'家数在 {cnt.group(1)}、钱在 {amt.group(2)} —— 两个母体，判据不同批'
        assert 'coalesce' not in core[amt.start():amt.end()].lower(), \
            '缺行被补成 0 ＝ "一票没有"与"成交额 0"混成同一个值'


def test_回算聚合与每日链用同一套FILTER判据():
    """`arms_agg_sql()`（全史回算那条）与 `CORE_SQL` 的 `today` 判据逐字一致。

    回算与每日链不同源，后果是"回填过的历史"与"每天新落的那行"两套口径，
    对账永远追不平。
    """
    from app.compute import breadth

    agg = _flat(breadth.arms_agg_sql())
    core = _flat(breadth.CORE_SQL)
    for sign in ('>', '<'):
        pat = r'sum\((?:t\.)?amount_yuan\) FILTER \(WHERE (?:t\.)?pct_change %s 0\)' % sign
        assert re.search(pat, agg), f'回算那条少了 pct_change {sign} 0 的合计'
        assert re.search(pat, core), f'每日链少了 pct_change {sign} 0 的合计'
    assert 'pct_change IS NOT NULL' in agg, '回算没排除 pct_change 为空的行＝母体比家数那批宽'
    assert 'FROM market_data.stock_daily' in agg


# ---------------------------------------------------------------- ③④ 真库：不越总额、逐位复算

def test_真库_涨额加跌额不超过当日全市场成交额合计():
    """全史每一场：`up_amount + down_amount ≤ 当日全部票的成交额合计`。

    这条抓的是"FILTER 方向条件写漏"——把全天的额加进了分子，两个合计相加必超总额。
    总额那一路**故意不排除 `pct_change IS NULL` 的行**：它是更宽的母体，
    所以"不超"是必然成立的恒等式，破了就是 SQL 写错。
    """
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    bad = pg.fetch_df("""
        WITH tot AS (SELECT trade_date, sum(amount_yuan) AS total_amount
                       FROM market_data.stock_daily GROUP BY 1)
        SELECT count(*)::int AS n,
               count(*) FILTER (WHERE arms_ratio::text IN ('NaN', 'Infinity', '-Infinity'))::int
                   AS bad_text,
               count(*) FILTER (WHERE arms_ratio = 0)::int AS zero_rows,
               count(*) FILTER (WHERE arms_ratio IS NOT NULL
                                 AND (up_amount IS NULL OR down_amount IS NULL))::int
                   AS ratio_without_both_sides
          FROM analytics.market_breadth b
          LEFT JOIN tot t USING (trade_date)
         WHERE coalesce(b.up_amount, 0) + coalesce(b.down_amount, 0) > coalesce(t.total_amount, 0)
    """)
    assert bad is not None and not bad.empty
    row = bad.iloc[0]
    assert int(row['n']) == 0, f'{row["n"]} 场涨额+跌额超过当日总成交额 —— 判据写成了另一个母体'
    assert int(row['bad_text']) == 0, '库里有 NaN / Infinity 的比值'
    assert int(row['zero_rows']) == 0, '库里有恰好为 0 的比值 —— 0 该由 NULL 表达'
    assert int(row['ratio_without_both_sides']) == 0, '两边不齐却写了比值'


def test_真库_随机三场与现算比值逐位对上():
    """随机取 3 场，把库里三个值与**直接从日线现聚合**的两条路逐位对上。

    - `up_amount` / `down_amount`：`::text` 比字符串，不经过 float（一过 float 就判不出
      分了位的漂）；
    - `arms_ratio`：库里存的、SQL 现算的、Python `Decimal` 高精度现算的三者必须一模一样。
    """
    if not pg.enabled():
        pytest.skip('本机库没通，这条要真库')
    pick = pg.fetch_df("""SELECT trade_date::text AS d FROM analytics.market_breadth
                           WHERE arms_ratio IS NOT NULL
                           ORDER BY random() LIMIT 3""")
    assert pick is not None and len(pick) == 3, pick
    for d in list(pick['d']):
        stored = pg.fetch_df("""SELECT up_amount::text ua, down_amount::text da,
                                      arms_ratio::text r
                                 FROM analytics.market_breadth WHERE trade_date = %s""", (d,)).iloc[0]
        raw = pg.fetch_df("""SELECT sum(amount_yuan) FILTER (WHERE pct_change > 0)::text ua,
                                   sum(amount_yuan) FILTER (WHERE pct_change < 0)::text da
                              FROM market_data.stock_daily
                             WHERE trade_date = %s AND pct_change IS NOT NULL""", (d,)).iloc[0]
        assert str(stored['ua']) == str(raw['ua']), f'{d} 涨额与现算不符：{stored["ua"]} vs {raw["ua"]}'
        assert str(stored['da']) == str(raw['da']), f'{d} 跌额与现算不符：{stored["da"]} vs {raw["da"]}'
        assert raw['da'] is not None and Decimal(str(raw['da'])) > 0, f'{d} 这一场本应有跌额'
        with localcontext() as ctx:
            ctx.prec = 60       # 比 PG 数值除法的内部位数更宽，避免双重舍入掩盖真差异
            want = (Decimal(str(raw['ua'])) / Decimal(str(raw['da']))).quantize(
                Decimal('0.000001'), rounding=ROUND_HALF_UP)
        assert str(stored['r']) == str(want), f'{d} 比值不是逐位一致：{stored["r"]} vs {want}'

        # 第三条路：每日链那条 CORE_SQL（一天一场的写法）必须与回算的集合式给同一个数。
        # 链路的 `arms_ratio` 是在 upsert 里由 `NULLIF` 现算的，`compute()` 只交回两个合计，
        # 所以这里比的是两个合计 —— 它们才是"同一批票"的那个东西。
        import datetime as dt
        from app.compute import breadth
        c = breadth.compute(dt.date.fromisoformat(d))
        assert c is not None, f'{d} 每日链算不出来'
        assert str(c['up_amount']) == str(stored['ua']), \
            f'{d} 每日链的涨额 {c["up_amount"]} 与库里的 {stored["ua"]} 不同源'
        assert str(c['down_amount']) == str(stored['da']), \
            f'{d} 每日链的跌额 {c["down_amount"]} 与库里的 {stored["da"]} 不同源'
        assert c['up_count'] > 0 and c['down_count'] > 0, f'{d} 这一场涨跌家数都该非零'
        # "同一批票"不是嘴上说的：家数与成交额来自同一次聚合，逐场对得上才算数
        cnt = pg.fetch_df("""SELECT count(*) FILTER (WHERE pct_change > 0)::int uc,
                                   count(*) FILTER (WHERE pct_change < 0)::int dc
                              FROM market_data.stock_daily
                             WHERE trade_date = %s AND pct_change IS NOT NULL""", (d,)).iloc[0]
        mb = pg.fetch_df('SELECT up_count::int uc, down_count::int dc FROM analytics.market_breadth '
                         'WHERE trade_date = %s', (d,)).iloc[0]
        assert int(mb['uc']) == int(cnt['uc']) == int(c['up_count']), \
            f'{d} 涨家数三个来源不一致：库 {mb["uc"]} / 现算 {cnt["uc"]} / 链路 {c["up_count"]}'
        assert int(mb['dc']) == int(cnt['dc']) == int(c['down_count']), \
            f'{d} 跌家数三个来源不一致：库 {mb["dc"]} / 现算 {cnt["dc"]} / 链路 {c["down_count"]}'
