# -*- coding: utf-8 -*-
"""一键追平（e1_fill_all）失败归因逻辑的单元测试。

只测纯函数 `classify_failure`，不跑子脚本：它负责把"退出码 + 输出末尾"
转成一句人能看懂的原因，是「数据不全 vs 网络 vs 无权限」区分的关键。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope='module')
def cls():
    # e1 顶层会 import app.db，项目测试套件里 DB 可达，正常导入即可。
    from scripts.ops import e1_fill_all
    return e1_fill_all


def test_daily_rc2_is_design_stop(cls):
    assert '按设计停步' in cls.classify_failure('daily', 2, '')


def test_no_permission(cls):
    tail = '单据返回 40203 没有接口访问权限'
    assert '无权限' in cls.classify_failure('margin', 1, tail)
    assert '无权限' in cls.classify_failure('lhb', 1, 'HTTP 401')


def test_network(cls):
    tail = 'requests.exceptions.ConnectionError RemoteDisconnected'
    assert '网络' in cls.classify_failure('fundflow', 1, tail)
    assert '网络' in cls.classify_failure('etf', 1, 'Error: Connection refused')


def test_incomplete_data(cls):
    assert '数据不全' in cls.classify_failure('margin', 1, '两所均为空（非交易日或尚未发布）')
    assert '数据不全' in cls.classify_failure('c10', 1, '查不到 0 行')


def test_timeout(cls):
    assert '超时' in cls.classify_failure('daily', 1, 'subprocess.TimeoutExpired')


def test_unknown(cls):
    assert '脚本异常' in cls.classify_failure('share', 9, 'generic crash noise')