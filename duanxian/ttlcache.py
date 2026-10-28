# -*- coding: utf-8 -*-
"""出网取数的共享 TTL 缓存：成功缓存久一点，**空与失败也只缓存一小会儿**。

为什么要有这一份（2026-10-25 实测，首页加载排查）：

1. 原来 `vr/market._cached` 的做法是"空结果不缓存，下次直接重试"。听着对，实际后果是
   **对端不通时，每个访客的每一发都重付一遍超时**：`/api/global/indices` 常年 12.4 秒、
   只回 11 字节的空数组，首页那一块因此一直是空的，还占着浏览器对同一站点的 6 条连接之一。
2. 没有锁：首页两处组件各要一遍同一个接口（`SummaryBoard` 与 `MarketPanorama` 都要 overview），
   同时 miss 就打两遍外网 —— 对面是我们依赖的免费源，加倍打它会直接把限流打出来。
3. 空与失败要能被界面说出来：`status(key)` 交「这次是没读到，还是今天真的没有」+ 上次成功的时刻。
   空数组直接交给界面 = 拿"取不到"冒充"没有"，那是假读数（同全站判据不许在出错时返回空值）。

键由各调用方自带前缀（如 `market:emotion`、`overseas:snapshot`），互不相干；
测试用 `clear()` 复位，不要去手撕 `_STORE`。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, Tuple

BEIJING = timezone(timedelta(hours=8))

DEFAULT_TTL = 300         # 成功结果：5 分钟（免费公开源的盘中节奏）
DEFAULT_FAIL_TTL = 60     # 空结果 / 抛异常：60 秒内不再重复出网

_STORE: Dict[str, Tuple[float, Any, int]] = {}     # key -> (到期基准时刻, 值, 这一档 TTL)
_STATUS: Dict[str, dict] = {}
_LOCKS: Dict[str, threading.Lock] = {}
_GUARD = threading.Lock()
_FAILED = object()                                   # "刚才炸了"的哨兵，不与"源给了空表"混


def _lock_for(key: str) -> threading.Lock:
    with _GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _stamp() -> str:
    return datetime.now(BEIJING).isoformat(timespec='seconds')


def _hit(key: str, now: float) -> Optional[Any]:
    """命中就返回**缓存里那份值**；未命中返回 None。命中的失败哨兵要出声，不能静默。"""
    got = _STORE.get(key)
    if not got or now - got[0] >= got[2]:
        return None
    if got[1] is _FAILED:
        raise RuntimeError('%d 秒内这一路数据源没通（上一发：%s）—— 本轮不再重复出网'
                           % (got[2], (_STATUS.get(key) or {}).get('reason', '未记录')))
    return got[1]


def cached(key: str, fn: Callable[[], Any], *, valid: Callable[[Any], bool] = bool,
           ttl: int = DEFAULT_TTL, fail_ttl: int = DEFAULT_FAIL_TTL) -> Any:
    """跑 `fn()`，按 `valid` 决定这一档缓存多久；失败也记一段时间并原样抛出。

    ⚠️ `valid` 判的是"这轮取数算不算成功"，不是"数据好不好看"：
    空表 / 全零的 dict 是**可能是真的没有**（例如休市），也可能是源挂了 ——
    两者都按 `fail_ttl` 短缓存，并由 `status()` 把话说清楚，不假装成长期结论。
    """
    now = time.time()
    if (val := _hit(key, now)) is not None:
        return val
    with _lock_for(key):
        now = time.time()
        if (val := _hit(key, now)) is not None:      # 等锁期间别人可能已经取好了
            return val
        try:
            result = fn()
        except Exception as exc:                    # noqa: BLE001 —— 出声，但下一发不再重付超时
            _STORE[key] = (now, _FAILED, fail_ttl)
            _STATUS[key] = {'ok': False, 'failed': True, 'empty': False,
                            'reason': '%s: %s' % (type(exc).__name__, str(exc)[:160]),
                            'at': _stamp(), 'ttl': fail_ttl}
            raise
        good = bool(valid(result))
        _STORE[key] = (now, result, ttl if good else fail_ttl)
        _STATUS[key] = {'ok': good, 'failed': False, 'empty': not good,
                        'reason': None if good else '数据源这一轮返回空 —— 不等于今天没有',
                        'at': _stamp(), 'ttl': ttl if good else fail_ttl}
        return result


def status(key: str) -> dict:
    """这一路最近一次真实情况：`ok` / 是不是空 / 原因 / 时刻 / 这一档缓存多久。"""
    got = _STATUS.get(key)
    return dict(got) if got else {'ok': False, 'reason': '本轮还没取过', 'at': None,
                                  'failed': False, 'empty': False}


def clear(prefix: str = "") -> int:
    """清缓存（可只清某个前缀），返回清掉几条。测试与"刚换源想立刻看新数"用它。"""
    keys = [k for k in list(_STORE) + list(_STATUS) if not prefix or k.startswith(prefix)]
    for k in keys:
        _STORE.pop(k, None)
        _STATUS.pop(k, None)
    return len(keys)
