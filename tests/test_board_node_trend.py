# -*- coding: utf-8 -*-
"""节点分析与趋势股形状榜的判据自洽测试。

钉的是两件**本机真踩过**的事，不是格式洁癖：

1. **连续段的分组写法**。早先两条 SQL 都写成"掉线那一天组号 +1，然后在含掉线行的分区里
   `row_number()`"，结果掉线那天和下一段并进同一组 ⇒ 连着天数与第几板**整体偏 1**，
   本轮首板日还会指到一根根本没封板的 K 线。实测症状：2026-10-15 上证跌 1.586%，
   涨停 36 只里"首板行数 = 0"（全是 2 板），节点 1 一只都不命中 —— 而那天明明有逆势首板。
   正确写法是段键 `rn - cum_*` 且**先筛掉不满足的行、再开窗**。
2. **"判不了"与"没命中"必须在数据层就分开**。缺项一律 NULL + `hit_reason` 短码；
   不许兜成 0 或 false，否则界面会把"这台机器没算到"念成"这批票都没命中"。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.compute import board_node as bn
from app.compute import trend_stock as ts


# ---------------------------------------------------------------- 纯判据：新股/无名/ST 判定

def test_新股名前缀只对_N_C_加汉字生效():
    assert bn._name_is_new_stock('N派瑞') is True
    assert bn._name_is_new_stock('C某某') is True
    assert bn._name_is_new_stock('N233') is False      # 字母后跟数字不是新股前缀
    assert bn._name_is_new_stock('宁波银行') is False   # 第二个字是汉字才算前缀
    assert bn._name_is_new_stock('C') is False
    assert bn._name_is_new_stock(None) is False


def test_没有中文名的票按代码认_不能拿代码冒充名称():
    # 这条正是来源口径点出的坑：拿代码冒充名称 ⇒ 它会被当成"非 ST"混进最高板梯队
    assert bn._name_missing('', '600519.SH') is True
    assert bn._name_missing(None, '600519.SH') is True
    assert bn._name_missing('600519', '600519.SH') is True
    assert bn._name_missing('600519.SH', '600519.SH') is True
    assert bn._name_missing('贵州茅台', '600519.SH') is False


def test_节点阈值与档位常量():
    assert bn.NODE1_SH_DROP == -1.0          # 上证当日跌幅 >= 1 个点
    assert bn.NODE2_MIN_HEIGHT == 6          # 前一交易日全场最高连板 >= 6
    assert bn.MIN_BARS_FOR_LADDER == 6       # 不足 6 根日线算新股
    assert ts.MIN_BARS == 9                  # 谈得上"连续 5 天各自过 MA5"的最少根数
    assert ts.SHAPE_WINDOWS == (5, 10)
    assert ts.NH_WINDOWS == (10, 20)
    assert '北交所' not in ts.BOARDS          # 北交所不纳入趋势股这一屏


# ---------------------------------------------------------------- SQL 写法：连续段分组

def test_连续段的两条_SQL_都用_先筛后开窗_那一种写法():
    """把踩过的坑钉在文本上：谁改回旧写法，这条就红。

    旧写法的特征是在同一个分区里既含不满足的行、又用 row_number 数位置；
    新写法的特征是先 `where coalesce(...)` 筛出满足的行，再按 `rn - cum_*` 分组数。
    """
    for mod, keys in ((bn, ('cum_seal',)), (ts, ('cum_above', 'cum_seal'))):
        sql = mod.CORE_SQL
        for k in keys:
            assert f'(rn - {k})' in sql, f'{mod.__name__} 的段键不再是 rn - {k}'
            assert f'PARTITION BY security_code, (rn - {k})' in sql, f'{mod.__name__} 分组键被改坏'
        # 段内 row_number 必须开在**已筛过的行**上（`WHERE coalesce(...)` 之后）
        assert sql.count('WHERE coalesce(') >= 1, f'{mod.__name__} 没有先筛掉不满足的行'
        assert 'g_above' not in sql and 'g_seal' not in sql, '又写回旧的那套"组号 +1"分组了'


def test_封住涨停的判据与容差不许改口径():
    assert board_seal_expr() == "d.close >= d.limit_up - 0.005"


def board_seal_expr() -> str:
    return bn.SEAL


# ---------------------------------------------------------------- reject 短码（读侧诚实性）

def test_没入选一定给得出理由_而_0_涨幅_是有效值():
    # 形状成立但当天涨停 ⇒ 理由要说涨停，不能说"形状不对"
    assert ts._reject(True, True, True, 0.0, 40) == 'sealed_today'
    # 当日涨幅 0.0 是**算得出来的平盘**，不许当成"没读到"
    assert ts._reject(True, False, False, 0.0, 40) == 'shape10_not'
    assert ts._reject(True, True, False, None, 40) == 'no_pct'
    assert ts._reject(False, False, False, 1.0, 3) == 'bars_lt_9,shape5_not'
    # 两条形状都不成立时只报 5 日那一档（更常读的那一档），不并排报两条
    assert ts._reject(False, False, False, 1.0, 40) == 'shape5_not'


@pytest.mark.parametrize('bad', [None, '', '  ', '600519', '600519.SH', '600519.sh'])
def test_名字是代码的行数得数得出来(bad):
    """名称列里出现 `600519` 这种值，读者第一反应是页面截断或链接坏了。"""
    assert bn._name_missing(bad, '600519.SH') is True
