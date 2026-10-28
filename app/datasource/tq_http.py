# -*- coding: utf-8 -*-
"""
通达信 TQ 的本地 JSON-RPC 通道（官方推荐路）。

**为什么另开一个模块而不是改 `app/tdx_client/tq_bridge.py`**：那条路是
"生成一个 .py 扔进 PYPlugins\\user → 起子进程 → import tqcenter → tq.initialize()"，
官方 Skill 文档（`docs/参考/tdx_skills/tdx-tq-local/SKILL.md` 核心规则 2~4）
现在**明文禁止**这么用，要求全部走 HTTP POST 到 `http://127.0.0.1:17709/`。
实测这条路也快得多（一次调用 0.03~0.6s，子进程那条每次要几秒起 Python）。
`tq_bridge` 留着当备用：客户端版本旧到没开 HTTP 服务时还得靠它。

口径三条，全部实测（2026-08-31）：
    1. `Amount` 的单位是**万元**，不是元。要元就乘 `AMOUNT_SCALE`。
    2. 不复权数据里带 `ForwardFactor`（前复权因子）；复权数据里没有。
    3. 底层返回是 `{'ErrorId': '0', 'Value': [...]}`，`ErrorId != '0'` 就是失败。
       但**有些接口直接返回数组/字符串**，所以 `_unwrap` 两种都要认。

只依赖标准库 urllib：这是本机回环调用，为它引一个 requests 不值得。

**HTTP 白名单窄于文档**：2026-08-31 逐个试过 SKILL.md 列的 34 个读接口，
`get_zdt_data`（涨跌停状态/打开次数）与 `get_exday_data`（扩展日线 L2）
返回 `-32601 MCP不支持该tqcenter方法名` —— 文档写了但服务端没暴露。
要这两样只能回退 `tq_bridge` 子进程路。其余 32 个都通。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from app.datasource import registry as reg
from app.datasource import tq_gate

#: JSON-RPC 端点。端口可以用 FT_TQ_HTTP_URL 覆盖（换客户端版本时可能变）
URL = os.environ.get('FT_TQ_HTTP_URL') or 'http://127.0.0.1:17709/'

#: 注册表里的 datasource_key。限流走 `free` 组 —— 本机回环，没有配额也不会被风控
DS_KEY = 'tq_http'

#: `Amount` 万元 → 元
AMOUNT_SCALE = 10_000

#: 默认超时。全市场 `get_stock_list` 实测 1s 内，分钟线 0.03s；
#: 留 60s 是给 `refresh_kline` 这类会真去下载的接口用的
TIMEOUT = 60.0


class TqHttpError(RuntimeError):
    """TQ 侧返回了 error 或 ErrorId != 0。带上 method 方便定位。"""

    def __init__(self, method: str, detail: str):
        self.method = method
        self.detail = detail
        super().__init__(f'{method}: {detail}')


def _post(method: str, params: dict | None, *, timeout: float,
          gate_timeout: float | None = None) -> object:
    # 每一次 TQ 调用都过全局串行闸（`app/datasource/tq_gate.py`）：TQ 是单连接的，
    # 并发打的失败方式是**返回空/少数据而不是报错**，事后看就像"那天本来就没有"。
    #
    # `gate_timeout` 是给**探活/只读小请求**留的：默认等 300s 对采集作业是对的
    # （排到队就该拿到数据），对 doctor 页面是错的 —— 那会把一次体检卡成 5 分钟，
    # 还把"别人正在用"报成"客户端没开"。
    with tq_gate.hold(who=method, timeout=gate_timeout):
        return _post_locked(method, params, timeout=timeout)


def _post_locked(method: str, params: dict | None, *, timeout: float) -> object:
    payload = {'id': 1, 'method': method, 'params': params or {}}
    req = urllib.request.Request(
        URL, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json; charset=utf-8'},
        method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode('utf-8'))
    if isinstance(body, dict) and body.get('error'):
        raise TqHttpError(method, str(body['error']))
    if not isinstance(body, dict) or 'result' not in body:
        raise TqHttpError(method, f'返回里没有 result：{str(body)[:200]}')
    return _unwrap(method, body['result'])


def _unwrap(method: str, result: object) -> object:
    """
    剥掉 `{'ErrorId','Value'}` 这层。

    不是所有接口都包这层（有的直接给数组），所以只在**认得出**的时候剥 ——
    盲目 `result['Value']` 会把直接返回数组的接口打成 TypeError。
    """
    if isinstance(result, dict) and 'ErrorId' in result:
        if str(result['ErrorId']) != '0':
            raise TqHttpError(method, f'ErrorId={result["ErrorId"]}')
        return result.get('Value')
    return result


def rpc(method: str, params: dict | None = None, *,
        timeout: float = TIMEOUT, required: bool = True) -> object:
    """
    调一个 TQ 接口，健康状态写回注册表。

    `required=False` 时失败返回 None —— 给"有则更好"的分量用
    （比如板块广度里的某一层板块），少一层该降级不该整批失败。
    """
    return reg.call(DS_KEY, _post, method, params, timeout=timeout,
                    required=required)


def available() -> tuple[bool, str]:
    """
    HTTP 服务通不通。返回 `(通, 说明)`，**不抛异常** —— 调用方常常是
    "通就走 TQ，不通就退回 vipdoc"，为这个写 try 太啰嗦。

    刻意不走 `rpc()`：探活失败不该把注册表刷成 down，那是 probe 的活。
    """
    try:
        v = _post('get_match_stkinfo', {'key_word': '600519'}, timeout=5.0,
                  gate_timeout=3.0)
    except tq_gate.TqBusy as exc:
        # 单独一支：把"别人正在用"和"客户端没开"混成一条，会让人去重启一个本来
        # 好好跑着的客户端，而真凶（卡死的作业 + 它持有的锁）永远查不到。
        return False, f'TQ 可用但正被占用：{exc}'
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f'连不上 {URL}（通达信没开或没启 TQ）：{exc}'
    except TqHttpError as exc:
        return False, str(exc)
    n = len(v) if hasattr(v, '__len__') else 0
    return bool(n), f'{URL} 通，get_match_stkinfo 返回 {n} 条'


# --------------------------------------------------------------- 行情
def market_data(codes: list[str], *, period: str = '1d',
                start: str | None = None, end: str | None = None,
                count: int = 0, dividend: str = 'none',
                fields: list[str] | None = None,
                required: bool = True) -> dict | None:
    """
    K 线 / 分钟线。返回 `{code: [{Date,Open,High,Low,Close,Volume,Amount,...}]}`。

    `count > 0` 时取截止 `end` 的最近 n 根，此时 `start` 被忽略（TQ 的口径）；
    `count <= 0` 才按 `start`/`end` 区间取。两个都传等于只有 count 生效，
    所以这里不做"贴心合并"，原样传下去。
    """
    params: dict = {'stock_list': codes, 'period': period,
                    'dividend_type': dividend, 'count': count,
                    'field_list': fields or []}
    if start:
        params['start_time'] = start
    if end:
        params['end_time'] = end
    return rpc('get_market_data', params, required=required)  # type: ignore[return-value]


def gb_info(code: str, dates: list[str], *, required: bool = True) -> object:

    """
    股本。`Zgb` 总股本 / `Ltgb` 流通股本 —— 市值快照缺口就靠这个补
    （市值 = 收盘价 × 股本，TQ 不直接给市值）。

    `date_list` 必须**从小到大**排好（文档要求），这里替调用方排一次：
    传乱序会静默拿到错的对应关系，比报错更难查。
    """
    ds = sorted(dates)
    return rpc('get_gb_info', {'stock_code': code, 'date_list': ds,
                               'count': len(ds)}, required=required)


# --------------------------------------------------------------- 板块
def sector_list(*, with_name: bool = True, required: bool = True) -> object:
    """板块清单。`list_type=1` 才带中文名（0 只给代码）。"""
    return rpc('get_sector_list', {'list_type': 1 if with_name else 0},
               required=required)


def sector_members(sector: str, *, with_name: bool = True,
                   required: bool = True) -> object:
    """板块成分股。`sector` 传板块代码或中文名都行（TQ 的 `block_code`）。"""
    return rpc('get_stock_list_in_sector',
               {'block_code': sector, 'list_type': 1 if with_name else 0},
               required=required)

