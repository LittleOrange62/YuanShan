# -*- coding: utf-8 -*-
"""逐票量能倍数（`analytics.volume_ratio_daily`）的判据与接线不变量。

这批测试**不连库也能跑**（除了明确标出的 skip 条件），钉的是四类会悄悄坏掉的东西：

1. 纯判据 `ratio_of`：缺分母必须交回 `(None, 原因)`。写成 0.0 会被读成"缩量到没有"，
   写成 1.0 会被读成"量没变" —— 两个都是假读数，而且页面上一眼看不出来。
2. 每日接线的结构不变量：`STAGES` 的名字与 `plan` 字典的键必须一一对应。
   少一个键 = 跑到那一步 KeyError，链断在半路；多一个键 = 那步永远不会被跑到，
   表现是"表建好了、脚本能跑、库里天天少一天"。
3. DDL 的 `spec_version` 必须在主键里（改口径是新写一批行，不改写历史 —— 全项目同一条约定）。
4. 文档与注释里指出的模块路径必须真实存在。
   `app/compute/pattern.py` 就留着一个指向不存在文件的"哨兵测试"注释，别再添一个。

跑法：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_volume_ratio.py -q`
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE = ROOT / 'app' / 'compute' / 'volume_ratio.py'
DDL = ROOT / 'app' / 'db' / 'sql' / '25_volume_ratio_daily.sql'
CHAIN = ROOT / 'ingest' / 'jobs' / 'e1_fill_all.py'

import sys
sys.path.insert(0, str(ROOT))
from app.compute import volume_ratio as vr  # noqa: E402


# ============================================================ 一、纯判据

@pytest.mark.parametrize('volume,prev,expect', [
    (200, 100, 2.0),                     # 放量一倍
    (100, 100, 1.0),                     # 量没变 —— 这是**算出来的** 1.0，不是兜底值
    (50, 100, 0.5),                      # 缩量一半
    (1234567, 7654321, 0.1613),          # 真实形状：除不尽就四位收口
])
def test_倍数就是当日量除以上一根量(volume, prev, expect):
    value, missing = vr.ratio_of(volume, prev)
    assert missing is None
    assert value == pytest.approx(expect, abs=1e-4)


@pytest.mark.parametrize('volume,prev,reason', [
    (None, 100, 'no_volume'),            # 今天没量（行在但值缺）
    (100, None, 'no_prev_bar'),          # 窗口内没有上一根：本库首场 / 长期停牌
    (100, 0, 'zero_prev_volume'),        # 上一根量为零 —— 除不动，不编一个数
    (100, -5, 'zero_prev_volume'),       # 负量是脏数据，同样不进入除法
    ('abc', 100, 'unparseable_volume'),  # 类型不对就说类型不对
])
def test_算不出就交空值加原因而不是_0_或_1(volume, prev, reason):
    value, missing = vr.ratio_of(volume, prev)
    assert value is None, '缺数据绝不能落成 0.0 或 1.0：那会被读成真实读数'
    assert missing == reason


def test_原因与空值成对出现():
    """`ratio_of` 的返回契约：有值 ⇒ 无原因；无值 ⇒ 有原因。表里两列必须同步。"""
    for volume, prev in [(200, 100), (None, None), (0, 0), (10, None), (10, 0)]:
        value, missing = vr.ratio_of(volume, prev)
        assert (value is None) == (missing is not None), (volume, prev, value, missing)


# ============================================================ 二、每日接线

def _plan_keys() -> list[str]:
    tree = ast.parse(CHAIN.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == 'plan' for t in node.targets):
            return [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    raise AssertionError('e1_fill_all.py 里找不到 plan = {...}，接线方式变了，先看这一步怎么写')


def test_加工段的每一步都在_plan_里有入口_且没有多余入口():
    names = [s[0] for s in _stages()]
    keys = _plan_keys()
    assert set(names) == set(keys), (
        'STAGES 与 plan 不一致：少键会在跑到那一步时 KeyError（链断半路），'
        '多键等于那步永远不会被跑到（天天少一天）。\n'
        '只在 STAGES=%s\n只在 plan=%s' % (sorted(set(names) - set(keys)),
                                          sorted(set(keys) - set(names))))
    assert len(names) == len(set(names)), '步骤重名：--skip 与摘要会指错那一步'
    assert 'volratio' in names and 'volratio' in keys, '量能倍数没接进每日加工段'


def _stages():
    import ingest.jobs.e1_fill_all as e1
    return e1.STAGES


def test_量能倍数是非骨干步_且超时用实测数():
    import ingest.jobs.e1_fill_all as e1
    row = [s for s in e1.STAGES if s[0] == 'volratio']
    assert len(row) == 1
    assert row[0][2] is False, '分析产出缺一天不该让"追平"整条判失败'
    assert 'volratio' in e1.STEP_TIMEOUTS, (
        '没给超时就用默认 1800s —— 上游那句注释说得对：数值必须按实测给')
    assert e1.step_timeout('volratio') <= 900, '实测单日 8.7 秒，给到 900s 已经是最坏情况的余量'


def test_入口在自己模块里_不再包一层同名壳():
    """项目约定：判据的 `main()` 就在自己模块里。曾经按 d10_volume_ratio.py 派过活，
    那个文件不存在 —— 别再留这种指路指到空处的写法。"""
    src = CHAIN.read_text(encoding='utf-8')
    assert re.search(r"'volratio':\s*\('app\.compute\.volume_ratio'", src), \
        '加工段入口应直指判据模块（plan 里那一项）'
    assert 'd10_volume_ratio' not in src, '还指着那个不存在的包装脚本'
    assert hasattr(vr, 'main'), '判据模块自己没有 CLI 入口'
    with pytest.raises(SystemExit):
        vr.main(['--help'])       # argparse 在，参数面就是这一步的契约


# ============================================================ 三、DDL 与路径真实性

def test_ddl_主键含_spec_version_且缺值走_null():
    sql = DDL.read_text(encoding='utf-8')
    assert 'primary key (security_code, trade_date, spec_version)' in sql, \
        '判据版本必须进主键：换口径是新写一批行，不改写历史'
    assert re.search(r'vol_ratio\s+numeric', sql), '倍数列必须是 numeric（不是 float8，也不用整型）'
    assert 'missing' in sql, '算不出的原因要有位置，否则 NULL 无从解释'


@pytest.mark.parametrize('text', [DDL.read_text(encoding='utf-8'),
                                  MODULE.read_text(encoding='utf-8')])
def test_注释里指的模块路径必须真实存在(text):
    """扫 `.py` 路径引用并逐个查在不在。`app/compute/pattern.py` 里那条
    "见 tests/test_pattern_scan.py 那个哨兵"就是反面样本：文件不存在，注释还在传话。"""
    for rel in set(re.findall(r'(?:app|ingest|duanxian|vr|tests|frontend)/[\w/.\-]*\.py', text)):
        rel = rel.rstrip('.,;')
        assert (ROOT / rel).exists(), '注释指向不存在的文件：%s' % rel


def test_整段一次窗口扫描_不逐日回查上一根():
    """第一版按天找上一根，实测单日 45.3 秒、一年要三小时；现在是一条 INSERT..SELECT。
    这条断言防的是"把它改回逐日循环" —— 那种改法看着更清楚，跑起来是不可用的。"""
    assert 'LAG(' in vr.UPSERT_SQL and 'WINDOW w AS' in vr.UPSERT_SQL
    assert vr.UPSERT_SQL.count('INSERT INTO') == 1
    assert vr.SCAN_BACK_DAYS >= 60, '往前扫太少会把长期停牌的票判成 no_prev_bar，倍数悄悄变 NULL'


def test_区间参数会把扫描起点往前推():
    import datetime as dt
    p = vr._params(dt.date(2025, 10, 1), dt.date(2026, 9, 30))
    assert p['d_from'] == dt.date(2025, 10, 1) and p['d_to'] == dt.date(2026, 9, 30)
    assert p['scan_from'] < p['d_from'], '扫描起点必须早于输出区间，否则区间首日没有上一根'
    assert p['scan_to'] == p['d_to']


def test_文档里抄的加工段步数就是现在的步数():
    """`12-定时任务全景` / `05-调度宿主` 里那几处"加工 N 步"是手抄数字。
    加一步就腐坏成"文档说 26、实际 27"，而下一个人是按文档判断"今天少跑了没有"的。
    钉住它：动步骤的人必须顺手动文档（这次就是量能倍数那一步把三处一起改了）。"""
    n = len(_stages())
    quoted: list[tuple[str, int]] = []
    for rel in ('docs/系统配置工作指南/12-定时任务全景.md',
                'docs/系统配置工作指南/05-调度宿主.md'):
        text = (ROOT / rel).read_text(encoding='utf-8')
        quoted += [(rel, int(m)) for m in re.findall(r'加工(?:段)?\s*(\d+)\s*步', text)]
    assert quoted, '文档里找不到"加工 N 步"这句，改成什么样了？连带更新这条测试'
    for rel, num in quoted:
        assert num == n, '%s 写的是 %d 步，实际 %d 步 —— 改步骤请顺手改文档' % (rel, num, n)
