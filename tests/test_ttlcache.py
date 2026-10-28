# -*- coding: utf-8 -*-
"""出网取数缓存（`duanxian/ttlcache.py`）的行为契约。

这一族存在的理由是 2026-10-25 首页排查实测到的三件事，全部要能用例钉住：

1. **空结果不缓存 = 每个访客每一发都重付一遍超时**（`/api/global/indices` 常年 12.4 秒、
   只回 11 字节）。现在空与失败都按短档缓存，且**取不到要说得出来**（`status`）。
2. **没有锁 = 首页两处组件同时 miss 就打两遍外网**（overview 被 `SummaryBoard` 与
   `MarketPanorama` 各要一遍）。现在同 key 串行，只有一发出网。
3. 失败与"真的没有"是两件事：`ok=False` 分 `failed`（炸了）与 `empty`（源给空表）两档，
   界面对两者都不许显示成"今天没有"（同全站"判据不许在出错时兜成空值"那条纪律）。

跑法：`.venv/Scripts/python.exe -X utf8 -m pytest tests/test_ttlcache.py -q`
"""
from __future__ import annotations

import threading
import time

import pytest

from duanxian import ttlcache as T


@pytest.fixture(autouse=True)
def clean():
    T.clear()
    yield
    T.clear()


def test_成功结果按长档缓存_不再重复出网():
    calls = []

    def fetch():
        calls.append(1)
        return [{'a': 1}]

    assert T.cached('k', fetch) == [{'a': 1}]
    assert T.cached('k', fetch) == [{'a': 1}]
    assert len(calls) == 1, '第二发必须命中缓存'
    assert T.status('k') == {'ok': True, 'failed': False, 'empty': False, 'reason': None,
                             'at': T.status('k')['at'], 'ttl': T.DEFAULT_TTL}


def test_空结果只按短档缓存_而不是每分钟重付一遍超时():
    """这是这一族存在的头号理由：旧写法对空结果**一个字节都不缓存**。"""
    calls = []

    def empty():
        calls.append(1)
        return []

    assert T.cached('k', empty) == []
    assert T.cached('k', empty) == []
    assert len(calls) == 1, '空结果也要在 fail_ttl 内顶住，否则首页每次都重付十几秒'
    st = T.status('k')
    assert st['ok'] is False and st['empty'] is True and st['failed'] is False
    assert st['ttl'] == T.DEFAULT_FAIL_TTL < T.DEFAULT_TTL
    assert '不等于今天没有' in st['reason'], st


def test_短档到期后允许重试():
    """只缓存 60 秒不是永久封存：源恢复后要能自己出数，否则就变成"一次失败永远空白"。"""
    calls = []
    T.cached('k', lambda: calls.append(1) or [], fail_ttl=0)
    T.cached('k', lambda: calls.append(1) or [], fail_ttl=0)
    assert len(calls) == 2


def test_抛异常的第一发原样出声_第二发不再出网():
    calls = []

    def boom():
        calls.append(1)
        raise TimeoutError('对端超时')

    with pytest.raises(TimeoutError):
        T.cached('k', boom)
    assert len(calls) == 1
    with pytest.raises(RuntimeError) as got:
        T.cached('k', boom)
    assert len(calls) == 1, '失败档没到之前不许再打一次外网'
    assert 'TimeoutError' in str(got.value), '原因要带出来，不能只说"失败了"'
    assert T.status('k')['failed'] is True and T.status('k')['empty'] is False


def test_同一键并发只出一发网():
    """首页两处组件同时 miss 就是旧版的双打外网（免费源被打两遍是请它封我们）。"""
    calls = []
    gate = threading.Barrier(2)

    def slow():
        calls.append(1)
        time.sleep(0.25)
        return [{'x': 1}]

    def worker():
        gate.wait()
        T.cached('k', slow)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=5)
    assert len(calls) == 1, f'两发同时进来，只准有一发出网（实得 {len(calls)}）'


def test_键按前缀分家且clear能只清一族():
    T.cached('a:1', lambda: [1])
    T.cached('b:1', lambda: [2])
    assert T.clear('a:') == 2                      # 值 + 状态两条
    assert T.status('a:1')['reason'] == '本轮还没取过'
    assert T.status('b:1')['ok'] is True


# --------------------------------------------------------------- 接线（真库不打桩）

def test_market_那一层不再自带第二套缓存():
    """同一件事两处实现迟早分叉成"两边 TTL 不一样"。判据是源码里真的只有一份。"""
    import pathlib
    import sys
    from pathlib import Path

    vr = Path(__file__).resolve().parents[1] / "vr"
    if str(vr) not in sys.path:
        sys.path.append(str(vr))        # 追加，不插 0（理由见 `_gstock` 那段）
    src = (vr / 'market.py').read_text(encoding='utf-8')
    assert 'from duanxian import ttlcache' in src
    for banned in ('_CACHE: dict', 'threading.Lock()', 'time.time()'):
        assert banned not in src, f'market 里还留着第二套缓存的零件：{banned}'
    import market                                  # vr/ 在路径上才是这个模块（与全站测试同一写法）

    T.clear()
    assert market._cached('probe', lambda: []) == []
    assert T.status('market:probe')['ok'] is False, '键要带 market: 前缀落进共享那一份里'


def test_overseas_的失败不会被当成长期结论():
    """`overseas_snapshot()` 失败时返回的是**非空字典** ⇒ valid 必须判 available，不能用 bool(v)。"""
    import pathlib

    src = pathlib.Path('duanxian/overseas.py').read_text(encoding='utf-8')
    assert "valid=lambda v: bool(v.get('available'))" in src, '用 bool(v) 会把"取不到"当成功缓存 5 分钟'
    assert 'ttlcache.cached' in src


def test_全球指数端点把状态交出去():
    """空数组单独交出 = 界面只能说"今天没有外围行情"，那是假读数。"""
    import pathlib

    src = pathlib.Path('vr/app.py').read_text(encoding='utf-8')
    assert '"status": market.cache_status(\'global_indices\')' in src


# --------------------------------------------------------------- 出网预算（全球指数那一榜）

def _gstock():
    """按全站写法把 vr/ 放进路径再 import（`import vr.gstock` 在这棵树里不是那个模块）。

    ⚠️ 只能**追加到末尾**：仓库根有 `app/` 包、vr 里有 `app.py`，插到 0 会让后面任何
    `from app.compute import …` 拿错对象（见 tests/test_first_board_sentiment.py 头部那段）。
    """
    import sys
    from pathlib import Path

    vr = str(Path(__file__).resolve().parents[1] / "vr")
    if vr not in sys.path:
        sys.path.append(vr)
    import gstock
    return gstock


class _假钟:
    """每探一次走 `step` 秒。真超时的形状就是「一次几秒」，用它把预算那一闸变成可确定的事。"""

    def __init__(self, step=0.0):
        self.t = 0.0
        self.step = step

    def monotonic(self):
        self.t += self.step
        return self.t


def _假源(缺的档数, 每档耗时=0.0):
    """前 `缺的档数` 档读不到、其余能读到的源；同时记下每一发实际用的超时。"""
    calls = []

    def 探(secid, fields, timeout=10):
        calls.append((secid, timeout))
        if len(calls) <= 缺的档数:
            return None
        return {"f43": 100, "f57": "X", "f58": "假指数", "f59": 2, "f60": 90, "f170": 120}
    return 探, calls


def test_全球指数榜_单档读不到不连带把后面的档判空(monkeypatch):
    """实测过的修法边界：旧写法五档串行各等 10 秒（冷发 54 秒），最省事的做法是"输两轮就收摊"。
    但那会把「道指/标普这一场恰好没数、恒生三档是好的」收成**空数组** —— 屏幕由"缺两档"变成"全空"，
    是一句假读数。所以收摊只看墙上时钟，不看失败次数。"""
    g = _gstock()
    源, calls = _假源(2)
    monkeypatch.setattr(g, "_push2_stock_get", 源)
    monkeypatch.setattr(g, "time", _假钟(0.0))    # 通道"快"：每次耗时 0，预算永远用不完
    rows = g.global_indices()
    assert len(rows) == len(g._INDICES) - 2, '前两档没数只该跳过那两档，后三档是真数'
    assert len(calls) == len(g._INDICES), '快通道不该提前收摊 —— 每一档都要试到'


def test_全球指数榜_通道慢时预算用完只交已经读到的档(monkeypatch):
    """预算这一闸要真的会落下：慢通道下不许把剩下的档继续挨个等完。"""
    g = _gstock()
    源, calls = _假源(0)
    monkeypatch.setattr(g, "_push2_stock_get", 源)
    monkeypatch.setattr(g, "time", _假钟(4.0))    # 每探一次走 4 秒 ⇒ 第二档就该超预算
    rows = g.global_indices()
    assert 0 < len(rows) < len(g._INDICES), f'慢通道要交"部分真数"，实测 {len(rows)} 行'
    assert len(calls) == len(rows), '超预算之后不该再往外发'


def test_全球指数榜用的超时是这一条自己的(monkeypatch):
    """查一只票是人在等（10 秒可以），首页那一榜是每 60 秒重付一次的自动请求 ⇒ 不能共用一把尺子。"""
    g = _gstock()
    源, calls = _假源(1)
    monkeypatch.setattr(g, "_push2_stock_get", 源)
    monkeypatch.setattr(g, "time", _假钟(0.0))
    g.global_indices()
    assert calls, '一档都没试就不算测到这条路径'
    assert {t for _, t in calls} == {g._INDEX_TIMEOUT}, '全球指数这一路要统一用自己的短超时'
    assert g._INDEX_TIMEOUT < 10, '这一路的超时必须比查票那一把短，否则五档串行又是半分钟'
