# -*- coding: utf-8 -*-
"""
计算层验收测试：品种归类、脚本注册表、表达式白名单、板块数据同步。

这些都是"不报错但算错"的地方 —— 归类错一个代码段就少 1,218 行涨跌停，
板块日线落后 4 天就让 sector_heat 静默变成 0 只，都不会抛异常。
"""
from __future__ import annotations

import pytest

from app.compute import expr as ex
from app.compute import scoring
from app.db import engine
from app.db.repo import security as sec_repo
from app.ingest import instrument as ins


@pytest.mark.parametrize(('code', 'kind'), [
    ('600519.SH', 'stock'),
    ('000001.SH', 'index'),
    ('000001.SZ', 'stock'),
    ('302132.SZ', 'stock'),     # 创业板 2024 年新增代码段
    ('920000.BJ', 'stock'),
    ('899050.BJ', 'index'),
    ('510010.SH', 'fund'),
    ('113050.SH', 'bond'),
    ('204001.SH', 'bond'),      # 国债逆回购，报的是年化利率不是价格
    ('900901.SH', 'bshare'),
    ('999997.SH', 'pseudo'),
    ('880301.SH', 'sector'),
])
def test_classify(code, kind):
    assert ins.classify(code) == kind


def test_302_is_chinext_not_main_board():
    """302132.SZ 历史上有 +20.04% 的交易日，走 20% 档而不是主板 10%。"""
    assert sec_repo.guess_board('302132', 'SZ') == '创业板'


def test_no_active_stock_lacks_board():
    """board 为空 → ref.board_limit_ratio 返回 NULL → 该票所有行都没有涨跌停价。"""
    rows = engine.query(
        "SELECT ts_code, name FROM ref.security "
        "WHERE asset_type = 'stock' AND board IS NULL LIMIT 10")
    assert not rows, f'个股缺 board：{rows}'


def test_bshare_and_pseudo_are_not_stock():
    """B 股以外币计价、999xxx 是通达信伪代码，都不能混进个股面板。"""
    rows = engine.query(
        "SELECT ts_code, asset_type FROM ref.security "
        "WHERE asset_type = 'stock' "
        "AND (symbol LIKE '900%' OR symbol LIKE '200%' OR symbol LIKE '999%' "
        "     OR symbol LIKE '204%') LIMIT 10")
    assert not rows, f'非个股被标成 stock：{rows}'


def test_all_registered_scripts_loadable():
    """config.script 里每一条都要能真的导进来，注册表和文件不能对不上。"""
    bad = []
    for r in engine.query(
            'SELECT script_key, path, entry FROM config.script WHERE enabled'):
        try:
            scoring.load_entry(r['path'], r['entry'])
        except Exception as exc:  # noqa: BLE001
            bad.append((r['script_key'], f'{type(exc).__name__}: {exc}'))
    assert not bad, f'脚本加载失败：{bad}'


@pytest.mark.parametrize('path', [
    '../../app/config.py', '/etc/passwd', 'builtin/../../evil.py',
    'evil.py', 'builtin/evil.txt',
])
def test_load_entry_rejects_paths_outside_builtin(path):
    """config.script.path 是页面上可编辑的字段，不能变成任意代码执行入口。"""
    with pytest.raises(ValueError):
        scoring.load_entry(path, 'run')


@pytest.mark.parametrize('x,y,want', [
    (10, 10, 'Q1'), (0, 0, 'Q1'),        # 边界 0 算强，跟 >= 的写法一致
    (10, -10, 'Q2'), (0, -0.01, 'Q2'),
    (-10, 10, 'Q3'), (-0.01, 0, 'Q3'),
    (-10, -10, 'Q4'),
    (None, 1, None), (1, None, None),
])
def test_quadrant_matches_seed_labels(x, y, want):
    """
    象限编号必须跟 `config.setting.quadrant_labels` 一致：
    Q1 买入 / Q2 持有 / Q3 试探 / Q4 回避。

    这条测试是为一个已经发生过的错误加的：第一版把 Q2/Q3/Q4 全排错了
    （Q2 写成 X<0 Y≥0、Q3 写成双弱、Q4 写成 X≥0 Y<0）。代码内部自洽，
    不抛异常，页面照样出数 —— 只是把该回避的票标成"持有"。
    """
    assert scoring.quadrant_of(x, y) == want


def test_quadrant_labels_agree_with_db():
    """代码里的中文标签和库里的种子值不能各说一套。"""
    row = engine.scalar(
        "SELECT value FROM config.setting WHERE key = 'quadrant_labels'")
    if row is None:
        pytest.skip('config.setting 里没有 quadrant_labels')
    assert row == scoring.QUADRANT_LABEL, \
        f'库里 {row} 与代码里 {scoring.QUADRANT_LABEL} 不一致'


def test_expr_rejects_unknown_names():
    for bad in ('__import__("os")', 'OPEN(close)', 'MA(secret_field, 5)'):
        with pytest.raises(ex.ExprError):
            ex.check(ex.normalize(bad))


def test_sector_daily_keeps_pace_with_stock_daily():
    """
    板块日线落后于个股日线时，板块热度 4 个分项全空，
    env_resonance 会悄悄退化成"只看大盘情绪"，评分照样出数。
    """
    gap = engine.scalar(
        'SELECT (SELECT max(trade_date) FROM md.stock_daily) '
        '- (SELECT max(trade_date) FROM md.sector_daily)', role='app')
    assert gap == 0, f'板块日线落后个股日线 {gap} 天'


def test_sector_daily_has_breadth_counts():
    """广度是板块热度里权重最高的一维，家数三列不能全是 NULL。"""
    latest = engine.scalar('SELECT max(trade_date) FROM md.sector_daily', role='app')
    row = engine.query(
        'SELECT count(*) AS n, count(up_count) AS c FROM md.sector_daily '
        'WHERE trade_date = %s', (latest,), role='app')[0]
    assert row['c'] > 0, f'{latest} 的 {row["n"]} 个板块全都没有涨跌家数'
