# -*- coding: utf-8 -*-
"""XY 合计名次落库那一环：口径、幂等、链上接线，与「不许长出第二份加法」。

用户 2026-10-28 的原话是「名次应该也在数据加工的时候入库了呀」＋「后续作为数据加工的一环要每天都跑」
⇒ 这一档不再是每个屏各现加一遍，而是 `analytics.xy_rank_daily`（`xyr-1`，加工段第 37 步 `xyrank`）。
钉住四件会静默说谎的事：
1. `rank_sum` 就是 `rank_x + rank_y`（两个**已入库名次**之和），不是"对合计再排一次名"，
   也不是 `oper_score` 的合成分名次；
2. 只有一天**两轴都有名次**才落行 ⇒ 少一轴是"没这行"（界面 `—`），绝不兜成 0 或"排在最后"；
3. 全链只有一处定义这两个 capability_key（`xy_rank.X_KEY/Y_KEY`）；
   「名次稳步前进榜」在自己的 SQL 里用同样的键，这条测试盯着它不许分叉；
4. 表里没有的（旧算法迁入批带名次）不许假装落过：`stale` 当前全库 0 行 true，
   因为那批行的 `rank_in_market` 是 NULL —— 这句要能被现查复算，不是注释里的一句传说。
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from app.compute import xy_rank as XY

ROOT = Path(__file__).resolve().parent.parent


def _db_up() -> bool:
    from app.db import engine
    try:
        return bool(engine.scalar('select 1'))
    except Exception:  # noqa: BLE001
        return False


# ============================================================ 一、SQL 与入参（不打库）

def test_一天一条语句里那三件事都在():
    """配两轴、算和、带上分母与旧批 —— 少任何一件，屏上那句话就没有出处。"""
    sql = XY._ONE_DAY
    assert 'max(s.rank_in_market) filter (where s.capability_key = %(x)s)' in sql
    assert 'max(s.rank_in_market) filter (where s.capability_key = %(y)s)' in sql
    assert 'p.rx + p.ry' in sql, '和必须在 INSERT..SELECT 里算，别回到读侧再算一遍'
    assert 'count(*) over ()' in sql, '当日两轴都有名次的只数＝分母，必须随行交出'
    assert 'where p.rx is not null and p.ry is not null' in sql, '少一轴就不落行（没有和可算）'
    flat = ' '.join(sql.split()).lower()
    assert 'on conflict (security_code, trade_date, spec_version) do update' in flat, \
        '重复跑必须幂等（每日链会连着补几天）'


def test_键名与规范号只有一份():
    assert (XY.X_KEY, XY.Y_KEY) == ('stock_quality', 'env_resonance')
    assert XY.SPEC == 'xyr-1'
    assert XY.TABLE == 'analytics.xy_rank_daily'
    # 「名次稳步前进榜」的 SQL 里用的是同样两个键 —— 它不 import 这里（那是另一屏的 SQL），
    # 所以只能靠这条测试钉住不许分叉（改了键名不改这里＝两屏悄悄算成两把尺）。
    q = (ROOT / 'duanxian' / 'quant.py').read_text(encoding='utf-8')
    steady = q[q.index('STEADY_METRICS'):]
    assert "'stock_quality'" in steady and "'env_resonance'" in steady, '稳步前进榜的 xy 两轴键名变了'
    assert 'rx + ry' in steady, '稳步前进榜那把 xy 尺仍是"两个名次相加"，不是别的'


def test_链上登记与执行入口都在():
    src = (ROOT / 'ingest' / 'jobs' / 'e1_fill_all.py').read_text(encoding='utf-8')
    import ingest.jobs.e1_fill_all as e1
    names = [s[0] for s in e1.STAGES]
    assert 'xyrank' in names, '没接进每日链 ⇒ 这张表从此不再更新，屏上"最近一场"会永远停在建表那天'
    assert names.index('xyrank') == names.index('scores') + 1, \
        '原料是 `scores` 刚写的 rank_in_market，排前面拿到的是昨天那份名次'
    assert re.search(r"'xyrank':\s*\('app\.compute\.xy_rank'", src), 'STAGES 有条目但没接执行入口'
    assert 'xyrank' in e1.STEP_TIMEOUTS
    row = next(s for s in e1.STAGES if s[0] == 'xyrank')
    assert row[2] is False, '判据产出缺一天不该把"追平"整条判失败'


def test_DDL_与覆盖体检都登记了():
    ddl = (ROOT / 'app' / 'db' / 'sql' / '57_xy_rank_daily.sql').read_text(encoding='utf-8')
    assert 'create table if not exists analytics.xy_rank_daily' in ddl
    assert 'primary key (security_code, trade_date, spec_version)' in ddl
    assert '越小越靠前' in ddl and 'rank_in_market' in ddl, '方向与"两个已入库名次"这两句必须写在列注释里'
    cov = (ROOT / 'duanxian' / 'coverage.py').read_text(encoding='utf-8')
    assert 'xy_rank_daily' in cov, '没登记进覆盖体检 ⇒ /coverage 说不出它滞后到哪天（自愈链少一环）'


# ============================================================ 二、真库核对（不打桩）

def test_写进去的每一行都自洽():
    if not _db_up():
        pytest.fail('库不通：这条核对必须连真库跑（rc=0 与日志都会说谎，只认库里的数）')
    from app.db import engine
    got = engine.query_one(
        "select count(*) n, count(distinct trade_date) s, min(trade_date) lo, max(trade_date) hi, "
        "       count(*) filter (where rank_sum <> rank_x + rank_y) bad_sum, "
        "       count(*) filter (where rank_x > universe or rank_y > universe) bad_rank, "
        "       count(*) filter (where rank_x < 1 or rank_y < 1) bad_low, "
        "       count(*) filter (where stale) stale "
        "  from analytics.xy_rank_daily where spec_version = %s", (XY.SPEC,), role='reader')
    assert got and int(got['n']) > 1_000_000, f'回补没落地？只有 {got and got["n"]} 行'
    assert int(got['bad_sum']) == 0, '有行的和不等于两个加数 —— 单位或写入顺序出错'
    assert int(got['bad_rank']) == 0, '有名次大于当日只数的行 ⇒ 名次与分母不是同一批'
    assert int(got['bad_low']) == 0, '名次从 1 开始，0 是兜出来的'
    assert str(got['lo'])[:10] == '2025-08-28', '起点应当是 score 里名次的最早一场，不插值不回填'
    # 注释里那句"旧批压根进不来"必须可复算：score 里 is_stale 的行没有一条有名次
    stale_rank = engine.scalar(
        "select count(*) from analytics.score where is_stale and rank_in_market is not null")
    assert int(stale_rank) == 0, '旧批哪天有了名次，DDL 与列注释里那句"当前全库 0 行 true"就要改口'
    assert int(got['stale']) == 0

    # 一场一场对：表里的行数 == 那天两轴都有名次的只数（少一轴不落行）
    smp = engine.query(
        "select trade_date from analytics.xy_rank_daily where spec_version = %s "
        "group by 1 order by 1 desc limit 3", (XY.SPEC,), role='reader')
    for r in smp:
        d = r['trade_date']
        stored = engine.scalar("select count(*) from analytics.xy_rank_daily "
                               "where spec_version = %s and trade_date = %s", (XY.SPEC, d))
        should = engine.scalar(
            """select count(*) from (
                 select security_code,
                        max(rank_in_market) filter (where capability_key = %s) rx,
                        max(rank_in_market) filter (where capability_key = %s) ry
                   from analytics.score
                  where trade_date = %s and capability_key in (%s, %s)
                    and rank_in_market is not null
                  group by 1) y where y.rx is not null and y.ry is not null""",
            (XY.X_KEY, XY.Y_KEY, d, XY.X_KEY, XY.Y_KEY))
        assert int(stored) == int(should), f'{d}：库里有名次的 {should} 只，表里落了 {stored} 行'


def test_读侧那一列交的是库里的数而不是现加的(monkeypatch):
    """首页首板分析那张表：`xy` 逐场带 day/rank/x/y/universe，缺库里的行就是空数组。"""
    from duanxian import first_board_daily as V
    d = dt.date(2026, 10, 28)
    calls = []

    def fake(sql, params=None, role='reader', **kw):
        text = ' '.join(str(sql).split())
        calls.append(text)
        if text.startswith('with days as (select trade_date from (select distinct trade_date from analytics.xy_rank_daily'):
            return [{'code': '600000', 'day': '2026-10-28', 'rank_x': 20, 'rank_y': 12,
                     'rank_sum': 32, 'universe': 5570, 'stale': False}]
        return []

    monkeypatch.setattr(V, '_safe_query', lambda sql, params=None, **kw: fake(sql, params))
    monkeypatch.setattr(V, '_full_map', lambda codes: {'600000': '600000.SH'})
    monkeypatch.setattr(V.BS, 'sealed_days_by_code', lambda codes, d: {})
    out = [{'code': '600000.SH', 'name': '测试'}]
    meta = V._enrich(out, d.isoformat(), [])
    assert meta['xy_spec'] == XY.SPEC and meta['xy_keys'] == {'x': XY.X_KEY, 'y': XY.Y_KEY}
    assert out[0]['xy'] == [{'day': '2026-10-28', 'rank': 32, 'x': 20, 'y': 12,
                             'universe': 5570, 'stale': False}]
    assert any('xy_rank_daily' in c for c in calls), '读侧必须去查那张表，不是从 score 现加'
    assert not any('rank_x + rank_y' in c.replace(' ', '') for c in calls), \
        '读侧不许再写一遍加法（那是第二份真相源）'


def test_库里整列没有时不许静默交一份假的日期清单(monkeypatch):
    """`xyrank` 那一步没跑过 ⇒ 这一列整列是 —。这里钉两件：
    ① `_enrich` 不交一份编出来的日期；② panel 里那条 hard 告警的文本在册（说清该跑哪条命令）。"""
    from duanxian import first_board_daily as V

    monkeypatch.setattr(V, '_safe_query', lambda sql, params=None, **kw: [])
    monkeypatch.setattr(V, '_full_map', lambda codes: {})
    meta = V._enrich([{'code': '600000.SH'}], '2026-10-28', [])
    assert meta.get('xy_dates') in (None, []), '一天都没有就别交一份假的日期清单'
    assert meta.get('xy_window_matches_quad') in (None, False)

    src = (ROOT / 'duanxian' / 'first_board_daily.py').read_text(encoding='utf-8')
    assert "'xy_empty'" in src and 'app.compute.xy_rank' in src, \
        '整列没有时那条告警要指名自救命令（缺数不许悄悄念成"这些票都没名次"）'
    assert "'xy_partial'" in src and "'xy_window_split'" in src, \
        '两种"读不到"要分开念：本场有几只缺行 / 两列的窗口分叉了'
