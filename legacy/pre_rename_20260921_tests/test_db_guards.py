# -*- coding: utf-8 -*-
"""
库层防护验收测试。

这是"取消 MCP 网关后校验仍然有效"的证明：
校验在数据库里，任何直连的脚本都绕不过去。
"""
from __future__ import annotations

import datetime as dt

import psycopg
import pytest

from app.db import engine
from app.db.repo import ops as ops_repo

TEST_CODE = '600000.SH'
TEST_DATE = dt.date(1990, 1, 2)   # 远离真实数据的哨兵日期


@pytest.fixture(scope='module')
def batch_id():
    engine.set_app_name('pytest_guards')
    bid = ops_repo.begin_batch('pytest', 'md.stock_daily', role='etl')
    yield bid
    engine.execute(
        'DELETE FROM md.stock_daily WHERE trade_date = %s AND src_batch_id = %s',
        (TEST_DATE, bid), role='app')
    ops_repo.end_batch(bid, status='ok', role='etl')


def _insert(bid, *, ts_code=TEST_CODE, open_=10.0, high=11.0, low=9.5, close=10.5,
            vol=1_000_000, amount=10_500_000.0, pct=1.0):
    """amount 单位元、vol 单位股。默认值的均价 10.5 落在 [low, high] 内。"""
    engine.execute(
        """
        INSERT INTO md.stock_daily
            (ts_code, trade_date, open, high, low, close, pre_close, pct_chg,
             vol_shares, amount_yuan, src_batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ts_code, trade_date) DO UPDATE SET
            amount_yuan = EXCLUDED.amount_yuan, vol_shares = EXCLUDED.vol_shares
        """,
        (ts_code, TEST_DATE, open_, high, low, close, 10.4, pct, vol, amount, bid),
        role='etl')


def test_valid_row_is_accepted(batch_id):
    _insert(batch_id)
    n = engine.scalar(
        'SELECT count(*) FROM md.stock_daily WHERE ts_code = %s AND trade_date = %s',
        (TEST_CODE, TEST_DATE))
    assert n == 1


def test_amount_unit_1000x_rejected(batch_id):
    """成交额若误按千元写入（数值缩小 1000 倍），均价会跌出价格区间。"""
    with pytest.raises(psycopg.errors.RaiseException) as exc:
        _insert(batch_id, amount=10_500.0)
    assert '单位口径异常' in str(exc.value)


def test_vol_unit_100x_rejected(batch_id):
    """成交量若误按手写入（数值缩小 100 倍），均价放大 100 倍。"""
    with pytest.raises(psycopg.errors.RaiseException) as exc:
        _insert(batch_id, vol=10_000)
    assert '单位口径异常' in str(exc.value)


def test_ohlc_order_enforced(batch_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(batch_id, low=12.0, high=11.0)


def test_sector_pseudo_code_rejected(batch_id):
    """880*/881* 是通达信板块伪代码，不是股票。旧系统靠事后脚本删。"""
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(batch_id, ts_code='880301.SH')


def test_bad_code_format_rejected(batch_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(batch_id, ts_code='ABCDEF.SH')


def test_pct_chg_bound_enforced(batch_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(batch_id, pct=5000.0)


def test_src_batch_id_is_mandatory():
    """没有批次号写不进去 —— 血缘强制。"""
    with pytest.raises(psycopg.errors.NotNullViolation):
        engine.execute(
            'INSERT INTO md.stock_daily (ts_code, trade_date, close) VALUES (%s, %s, %s)',
            (TEST_CODE, dt.date(1990, 1, 3), 10.0), role='etl')


def test_reader_cannot_write():
    """只读角色在库层就没有写权限，不依赖应用层自觉。"""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        engine.execute("UPDATE config.setting SET value = '1' WHERE key = 'web_port'",
                       role='reader')


def test_board_limit_ratio():
    """涨跌停按板块，不是一刀切 1.1。"""
    cases = [('主板', False, 0.10), ('主板', True, 0.05),
             ('创业板', False, 0.20), ('科创板', False, 0.20),
             ('北交所', False, 0.30)]
    for board, is_st, expected in cases:
        got = engine.scalar('SELECT ref.board_limit_ratio(%s, %s)', (board, is_st))
        assert float(got) == expected, f'{board} is_st={is_st}'


def test_effective_weights_normalized():
    """停用某个子因子后，剩余权重应自动补齐到 1。"""
    rows = engine.query(
        'SELECT cap_key, sum(eff_weight) AS s FROM config.v_effective_binding GROUP BY cap_key')
    assert rows, 'v_effective_binding 为空'
    for r in rows:
        assert abs(float(r['s']) - 1.0) < 1e-6, f'{r["cap_key"]} 权重和 {r["s"]}'


def test_config_history_records_changes():
    """config.* 的变更被触发器记入 ops.config_history，支持回滚。"""
    before = engine.scalar(
        "SELECT count(*) FROM ops.config_history WHERE table_name = 'config.setting'")
    engine.execute(
        "UPDATE config.setting SET value = value WHERE key = 'web_port'", role='app')
    after = engine.scalar(
        "SELECT count(*) FROM ops.config_history WHERE table_name = 'config.setting'")
    assert after > before


def test_registry_validate_has_no_errors():
    from app.db.repo import registry
    issues = registry.validate()
    errors = [i for i in issues if i['level'] == 'error']
    assert not errors, f'配置校验存在 error: {errors}'
