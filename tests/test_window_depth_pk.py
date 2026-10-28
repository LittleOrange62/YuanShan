# -*- coding: utf-8 -*-
"""两张"可调窗口深度"的表：主键必须含 `window_days`（10-18 实测踩出来的）。

现象：`startup_spike_days` / `startup_quadrant_track` 的界面都有 10/20/30 三档窗口控件。
主键里少了 `window_days` 时，跑一次 `--window 20` 会把同一批 (票·启动日·cohort[·分量]) 的
**30 场那批整行覆盖掉** —— 之后切回 30 档，接口老实回 `available:false`（库里就是没有），
屏幕上一句"这一段没有数据"，看着像覆盖问题，其实是主键问题。这条测试就是防它回来。

跑法：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_window_depth_pk.py -q`
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import engine                                    # noqa: E402
from app.compute import startup_spike_days as SS             # noqa: E402
from app.compute import startup_quadrant_track as SQ         # noqa: E402

TABLES = {
    'analytics.startup_spike_days': 'ssd-1',
    'analytics.startup_quadrant_track': 'sqt-1',
}


def _pk(table: str) -> list[str]:
    cols = engine.query(
        "select a.attname from pg_index i join pg_attribute a on a.attrelid=i.indrelid and a.attnum=any(i.indkey) "
        "where i.indrelid = %s::regclass and i.indisprimary", (table,), role='reader') or []
    return sorted(r['attname'] for r in cols)


@pytest.mark.parametrize('table', sorted(TABLES))
def test_主键必须含_window_days_否则换窗口深度会盖掉另一档(table):
    pk = _pk(table)
    assert 'window_days' in pk, f'{table} 主键 {pk} 少了 window_days ⇒ 三档窗口互相覆盖'
    assert 'spec_version' in pk, f'{table} 主键 {pk} 少了 spec_version ⇒ 换判据无法留旧批对账'


@pytest.mark.parametrize('table,spec', sorted(TABLES.items()))
def test_三档窗口在库里同时存在(table, spec):
    """只有一档有数＝回填没跑全，界面那个窗口控件就是假开关（同 `months` 那个假开关的坑）。"""
    if not engine.scalar('select 1'):
        pytest.fail('库不通：这条要连真库跑，不许静默跳过')
    rows = engine.query(
        f'select window_days, count(*) n from {table} where spec_version = %s group by 1 order by 1',
        (spec,), role='reader') or []
    got = {int(r['window_days']) for r in rows if r['n']}
    missing = {10, 20, 30} - got
    assert not missing, (f'{table} 缺窗口深度 {sorted(missing)}（现只有 {sorted(got)}）—— '
                         f'切到那一档界面只会说没数据，先把 --window 各跑一次')


def test_两张表的主键里那个窗口列不能只写在列清单里():
    """列里有、主键里没有＝最阴的一种：upsert 不报错，只是每次覆盖上一档。"""
    for mod, table in ((SS, 'analytics.startup_spike_days'), (SQ, 'analytics.startup_quadrant_track')):
        assert 'window_days' in mod.COLS, f'{table} 的 COLS 里没有 window_days'
        assert 'window_days' in mod._PK, f'{table} 的 _PK 没带 window_days（{mod._PK}）'
        assert f"on conflict ({', '.join(mod._PK)})" in mod.UPSERT, 'UPSERT 的冲突键不是那份主键'
        assert mod.UPSERT.count('%s') == len(mod.COLS), '占位符与列数分叉'


def test_ddl_与库里的索引和注释都还在():
    src = (ROOT / 'app/db/sql/44_startup_spike_days.sql').read_text(encoding='utf-8')
    assert 'primary key (security_code, launch_date, cohort, window_days, spec_version)' in src
    src2 = (ROOT / 'app/db/sql/43_startup_quadrant_track.sql').read_text(encoding='utf-8')
    assert 'primary key (security_code, launch_date, cohort, capability_key, window_days, spec_version)' in src2
    n = engine.scalar(
        "select count(*) from pg_attribute where attrelid='analytics.startup_spike_days'::regclass "
        "and attnum>0 and col_description(attrelid, attnum) is null")
    assert int(n) == 0, f'startup_spike_days 有 {n} 列没注释（页面 definitions 的出处就是列注释）'
