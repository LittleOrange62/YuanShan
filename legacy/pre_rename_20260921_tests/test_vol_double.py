# -*- coding: utf-8 -*-
"""
倍量震荡 `fct.vol_double`：近 40 个交易日频繁倍量 ＋ 沿 60 日线震荡。

这张表要撑住两件事，测试就盯这两件：

1. **数值可复算**：倍量次数、穿越次数、MA60 有效天数三项，用纯 Python 逐日重数
   一遍必须一致。这三个数最容易错在"基准含不含当日"、"MA60 满不满 60 根"、
   "ma60 为 NULL 的那几天算不算翻转"。
2. **"震荡"是四个条件同时成立**，不是只看贴得紧。只看偏离度会把"缓慢单边上行、
   股价一直压着均线"的趋势票选进来 —— 那和用户要的形态正好相反。

另外卡住两件工程上的事：页面必须读表不现算（现算 14 秒），
以及"声明=实现"（config.job 里有 calc_vol_double 就必须真能跑）。
"""
from __future__ import annotations

import pathlib
import time

import pytest

from app.compute import vol_double as vd
from app.db import engine
from app.orchestrator import runner
from app.web import queries

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope='module')
def day():
    d = vd.latest_date()
    if d is None:
        pytest.skip('fct.vol_double 是空的，先跑 d7_vol_double.py')
    return d


# ------------------------------------------------------------------ 数值可复算


def _recount(ts_code: str, d) -> tuple[int, int, int]:
    """拿这只票**自己的**最近 100 根 K 线，在 Python 里重数一遍。"""
    rows = engine.query(
        """
        SELECT trade_date, close, vol_shares FROM md.stock_daily
         WHERE ts_code = %(c)s AND trade_date <= %(d)s
           AND close IS NOT NULL AND vol_shares > 0
         ORDER BY trade_date DESC LIMIT %(span)s
        """, {'c': ts_code, 'd': d, 'span': vd.BARS_NEEDED})
    rows = list(reversed(rows))
    closes = [float(r['close']) for r in rows]
    vols = [float(r['vol_shares']) for r in rows]
    n = min(vd.LOOKBACK, len(rows))
    idx = list(range(len(rows) - n, len(rows)))

    dbl = 0
    for i in idx:
        base = vols[max(0, i - 5):i]          # 前 5 日，**不含当日**
        if len(base) == 5 and vols[i] >= vd.MULT * (sum(base) / 5):
            dbl += 1

    sides = []
    for i in idx:
        if i + 1 >= vd.MA_WIN:                # 满 60 根才算 MA60
            ma = sum(closes[i - vd.MA_WIN + 1:i + 1]) / vd.MA_WIN
            sides.append(1 if closes[i] >= ma else -1)
        else:
            sides.append(None)
    cross = sum(1 for a, b in zip(sides, sides[1:], strict=False)
                if a is not None and b is not None and a != b)
    return dbl, cross, sum(1 for s in sides if s is not None)


def test_counts_match_an_independent_recount(day):
    rows = engine.query(
        """
        SELECT ts_code, dbl_count, cross_count, ma60_days
          FROM fct.vol_double WHERE trade_date = %s
         ORDER BY dbl_count DESC, ts_code LIMIT 5
        """, (day,))
    assert rows, '当天一行都没有'
    for r in rows:
        dbl, cross, ma_days = _recount(r['ts_code'], day)
        assert r['dbl_count'] == dbl, (
            f"{r['ts_code']} 倍量次数：库里 {r['dbl_count']} / 复核 {dbl}"
            '（先查倍量基准是不是把当日算进去了）')
        assert r['cross_count'] == cross, (
            f"{r['ts_code']} 穿越次数：库里 {r['cross_count']} / 复核 {cross}")
        # 2026-08-31 踩过：取数按"市场交易日"切，MA60 要的是"这只票自己的 K 线
        # 根数"，停牌票会少几根 → ma60_days 偏低。FETCH_DAYS 的 50 天余量就是为这个。
        assert r['ma60_days'] == ma_days, (
            f"{r['ts_code']} MA60 有效天数：库里 {r['ma60_days']} / 复核 {ma_days}"
            '（停牌票要靠 FETCH_DAYS 的余量补，不够就会偏低）')


def test_double_volume_baseline_excludes_today():
    """基准含当日的话，当日巨量会把基准自己抬上去，倍量次数被系统性低估。"""
    src = (ROOT / 'app/compute/vol_double.py').read_text(encoding='utf-8')
    assert 'ROWS BETWEEN 5 PRECEDING\n' in src or '5 PRECEDING' in src
    assert '1 PRECEDING' in src, '倍量基准必须止于前一日，不能含当日'


def test_ma60_needs_a_full_window(day):
    """
    MA60 不满 60 根 K 线时记 NULL。**不置 0、不用手头几根凑**：
    凑出来的均线偏高，次新股会整片挤进榜单。
    """
    bad = engine.query(
        'SELECT ts_code, bars, ma60_days FROM fct.vol_double '
        'WHERE trade_date = %s AND ma60_days > bars', (day,))
    assert not bad, f'MA60 有效天数超过了有行情天数：{bad[:3]}'
    assert engine.scalar(
        'SELECT count(*) FROM fct.vol_double WHERE trade_date = %s '
        'AND ma60_days < %s', (day, vd.LOOKBACK)) > 0, \
        '一只次新股都没有？那多半是 MA60 用不足 60 根凑出来的'


def test_readings_are_within_common_sense(day):
    rows = engine.query(
        'SELECT * FROM fct.vol_double WHERE trade_date = %s', (day,))
    assert len(rows) > 4000, f'当天只有 {len(rows)} 只票，全市场应有五千多只'
    for r in rows:
        assert 0 <= r['dbl_count'] <= r['win_days']
        assert 0 <= r['cross_count'] <= r['win_days']
        assert r['bars'] <= r['win_days'], 'bars 不可能超过观察窗天数'
        if r['above_ratio'] is not None:
            assert 0 <= float(r['above_ratio']) <= 100
        if r['dev_med'] is not None and r['dev_max'] is not None:
            assert float(r['dev_med']) <= float(r['dev_max']), \
                '偏离中位数不可能大于最大偏离'
            assert float(r['dev_med']) >= 0


# ------------------------------------------------------------------ 入选口径


def test_picks_require_all_four_oscillation_conditions(day):
    """
    入选的每一只都必须同时满足四条。**只看偏离度不行** ——
    "股价一直压着上行的 60 线"贴得很紧，但那是趋势，不是用户要的震荡。
    """
    rows, meta = vd.picks(day)
    assert rows, '当天一只都没入选，先确认阈值不是被调死了'
    t = vd.thresholds()
    for r in rows:
        assert r['dbl_count'] >= t['min_dbl']
        assert r['cross_count'] >= t['cross_min'], '0~1 次穿越是单边不是震荡'
        assert float(r['dev_med']) <= t['dev_med_max']
        assert t['above_lo'] <= float(r['above_ratio']) <= t['above_hi'], \
            '线上占比 100% 或 0% 的票是单边'
        assert abs(float(r['ma60_chg'])) <= t['ma60_chg_max'], \
            '60 线自己在陡涨陡跌时，贴着线是趋势不是震荡'
        assert float(r['avg_amt_yi']) >= t['min_amt_yi']
        assert r['ma60_days'] >= t['min_ma60_days'], '次新股不该入选'
    assert meta['trade_date'] == day


def test_picks_are_sorted_by_double_volume_count(day):
    """用户点名要的排序：倍量次数从高到低，取前 50。"""
    rows, meta = vd.picks(day)
    counts = [r['dbl_count'] for r in rows]
    assert counts == sorted(counts, reverse=True)
    assert len(rows) <= meta['limit'] == 50


def test_thresholds_ignore_garbage(monkeypatch):
    """阈值要拼进 WHERE。非法值必须回到默认，不能让整张卡变成"无数据"。"""
    monkeypatch.setattr(vd.reg_repo, 'setting',
                        lambda *a, **k: {'min_dbl': 'DROP TABLE',
                                         'dev_med_max': -5,
                                         'cross_min': None,
                                         'nonsense': 1,
                                         'limit': 20})
    t = vd.thresholds()
    assert t['min_dbl'] == vd.FILTER_DEFAULTS['min_dbl']
    assert t['dev_med_max'] == vd.FILTER_DEFAULTS['dev_med_max']
    assert t['cross_min'] == vd.FILTER_DEFAULTS['cross_min']
    assert 'nonsense' not in t
    assert t['limit'] == 20, '合法的覆盖项要生效'


def test_thresholds_are_overridable_from_the_setting_row():
    """阈值走 config.setting，改了立刻生效、不用重算那 14 秒。"""
    row = engine.query_one(
        "SELECT value FROM config.setting WHERE key = 'vol_double.filter'")
    assert row, "config.setting 里没有 vol_double.filter"
    assert isinstance(row['value'], dict), '存的应当是 jsonb 对象（默认空对象）'


# ------------------------------------------------------------------ 页面


def test_card_reads_the_table_and_is_fast(day):
    """
    现算 14 秒，页面必须读表。**这条测的是"不阻塞"**：
    点一下卡半天等于这张卡不可用。
    """
    t0 = time.time()
    rows = queries.run('q_vol_double')
    el = time.time() - t0
    assert rows, 'q_vol_double 没有返回行'
    assert el < 1.5, f'读表用了 {el:.2f}s，说明它在现算而不是读 fct.vol_double'
    cols = set(rows[0])
    for c in ('ts_code', 'name', 'dbl_count', 'cross_count', 'dev_med',
              'above_ratio', 'ma60_chg', 'avg_amt_yi', 'trade_date', 'detail'):
        assert c in cols, f'榜单缺 {c} 这一列'
    # 基准日跟着每行走：日线到了但作业没跑时，看的人要能分清这是哪天的表
    assert all(r['trade_date'] == day for r in rows)


def test_card_is_registered_on_a_page():
    card = engine.query_one(
        "SELECT * FROM config.page_layout WHERE card_ref = 'q_vol_double'")
    assert card, 'config.page_layout 里没有这张卡'
    assert card['visible']
    assert card['card_kind'] == 'table'
    note = engine.query_one(
        "SELECT * FROM config.help_note WHERE target = 'q_vol_double'")
    assert note, 'config.help_note 里没有这张卡的说明'
    assert '前 5 个交易日均量' in (note['content'] or ''), \
        '说明里必须写清倍量的基准，否则看的人会以为是对昨日翻倍'


def test_column_labels_exist():
    from app.web import ui
    for c in ('dbl_count', 'dbl_prev_count', 'cross_count', 'dev_med',
              'dev_max', 'above_ratio', 'ma60_chg', 'avg_amt_yi', 'ma60_days'):
        assert c in ui._FALLBACK_LABEL, f'{c} 没有中文抬头'


# ------------------------------------------------------------------ 声明=实现


def test_job_is_declared_and_actually_runnable():
    job = engine.query_one(
        "SELECT * FROM config.job WHERE job_key = 'calc_vol_double'")
    assert job, 'config.job 里没有 calc_vol_double'
    assert job['enabled']
    assert 'md.stock_daily' in (job['ready_on'] or []), '应当由日线就绪触发'
    assert job['after_job'] is None, '它只读日线，不该挂评分依赖'
    name = runner.job_runner(job)
    assert name == 'compute_vol_double', '这个作业没有执行器'
    assert (ROOT / runner.RUNNERS[name]['script']).exists()


def test_fill_all_runs_it_but_only_for_one_day():
    """
    一天 14 秒，是这条链最慢的一步。**--recent 固定 1**：
    30 天 × 14 秒 = 7 分钟，会把「一键追平」拖成让人以为卡死的长任务。
    """
    src = (ROOT / 'scripts/ops/e1_fill_all.py').read_text(encoding='utf-8')
    assert "'voldbl'" in src, 'e1_fill_all 没有这一步'
    assert 'd7_vol_double.py' in src
    seg = src[src.index("'voldbl': ("):]
    assert "'--recent', '1'" in seg[:300], \
        '倍量震荡在追平链里应当只算目标日一天'
