# -*- coding: utf-8 -*-
"""
数据源注册表读写 + 带重试与**限流**的统一调用入口。

`call()` 是给摄取脚本用的：包一层重试、把结果写回 config.datasource，
这样"某个源什么时候开始不行了"在页面上看得见，而不是埋在某次跑批的日志里。

**2026-08-31 加了限流闸门**（见 `LIMITS` / `_gate`）：以前只有重试没有节流，
而东财是**有风控会封 IP** 的 —— 一旦被封，重试只会把封禁时间拉长，表现出来
就是"这个源今天一直 down"。阈值抄的是 a-stock-data 的社区实测值（2026-05）。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from app.db import engine

# 五种健康状态的语义见 03_registry.sql 的表注释
OK = 'ok'
DEGRADED = 'degraded'
NO_PERMISSION = 'no_permission'
BROKEN = 'broken'
DOWN = 'down'

# 这些状态是"已经查清楚的既定事实"，不该被一次探测/调用覆盖掉：
# 无权限不会因为重试就有权限，接口自身的 bug 也不会。
STICKY = (NO_PERMISSION, BROKEN)

# 层级 = 优先用谁。语义见 03_registry.sql 的表注释，中文名在 UI 上用。
# ★这是**口径**不是状态：ts_* 现在没权限、em_push2 现在连不上，层级照旧按
# "它本该担什么角色"填。两者混一列，一次网络抖动就会把主力源降成辅助源。
LAYERS = ('p0', 'main', 'base', 'assist')
LAYER_LABEL = {'p0': 'P0 本地', 'main': '主力层', 'base': '基座层',
               'assist': '辅助层'}
LAYER_RANK = {k: i for i, k in enumerate(LAYERS)}

RETRY = 3
BACKOFF = 1.5


# --------------------------------------------------------------- 限流闸门
# 一个限流组的三道约束：最小请求间隔、1 分钟上限、5 分钟上限。
# 三道一起用而不是只留一道：东财的风控是**多个维度同时看**的，只压 QPS
# 仍然会被 5 分钟总量掀掉（实测阈值见下）。
class RateLimit:
    __slots__ = ('min_interval', 'per_min', 'per_5min')

    def __init__(self, min_interval: float = 0.0, per_min: int | None = None,
                 per_5min: int | None = None):
        self.min_interval = min_interval
        self.per_min = per_min
        self.per_5min = per_5min


# 各限流组的阈值。
#
# `eastmoney` 那组抄的是 a-stock-data 记录的社区实测值（2026-05）：
# **> 5 请求/秒、单 IP 并发 ≥ 10、1 分钟 ≥ 200 次、5 分钟 ≥ 300 次会触发封禁**。
# 这里全部按七折留余量（0.25s 间隔 ≈ 4 QPS、1 分钟 140、5 分钟 210）：
# 被封一次要等几十分钟，跑批慢十几秒和被封一小时之间没什么可犹豫的。
#
# 本地文件源与腾讯**刻意不限流**：前者不出网，后者实测不封 IP，
# 给它们加间隔只会让全市场批量取数白慢几分钟。
LIMITS: dict[str, RateLimit] = {
    'eastmoney': RateLimit(min_interval=0.25, per_min=140, per_5min=210),
    'sina': RateLimit(min_interval=0.15, per_min=200),
    'ths': RateLimit(min_interval=0.30, per_min=120),
    'tushare': RateLimit(min_interval=1.0, per_min=1),     # 见下：**每接口** 1 次/分钟
    'free': RateLimit(),                                   # 不限
}

# Tushare 的配额是**按接口**算的，不是按账号算的。2026-08-31 实测这个 token：
# 连着打 daily → daily_basic → adj_factor → stock_basic 四个不同接口全过，
# 而同一个 daily_basic 隔十几秒再打就报「频率超限(1次/分钟)」，
# **再撞一次惩罚会升级成「1次/小时」**。所以 `limit_group()` 给 tushare 返回的是
# `tushare:<ds_key>`，每个接口各自一个滑动窗口；`LIMITS['tushare']` 是**每接口**阈值。
# 别把它改回按账号 50 次/分钟 —— 那是文档上的档位，跟实际拿到的权限不是一回事。
# 也别把 Tushare 放进任何跑批：这个配额只够"一天手动补一次"。



# ds_key → 限流组。**按 key 匹配，不按域名猜**：同一个源在库里可能登记成
# 好几个 ds_key（`ak_concept_em` / `ak_fundflow_em` / `em_push2` 都是东财），
# 它们共用同一个 IP 配额，必须落到同一个组里，否则三个 key 各自限流、
# 加起来照样超。
_GROUP_BY_KEY: dict[str, str] = {
    'em_push2': 'eastmoney',
    'em_datacenter': 'eastmoney',
    'ak_concept_em': 'eastmoney',
    'ak_fundflow_em': 'eastmoney',
    'ak_lhb_detail': 'eastmoney',
    'ak_lhb_inst': 'eastmoney',
    'alphafeed': 'free',
    'miaoxiang': 'free',
}


def limit_group(ds_key: str) -> str:
    """这个源属于哪个限流组。规则写在这一个地方，页面和脚本都问它。"""
    if ds_key in _GROUP_BY_KEY:
        return _GROUP_BY_KEY[ds_key]
    if ds_key.endswith('_em') or ds_key.startswith('em_'):
        return 'eastmoney'
    if ds_key.startswith('ts_') or ds_key.startswith('tushare'):
        # 每个接口一个独立配额，所以组名带上 ds_key。阈值仍读 `rate_limit.tushare`
        return f'tushare:{ds_key}'

    if ds_key.startswith('ths_') and not ds_key.startswith('ths_local'):
        return 'ths'
    if 'sina' in ds_key:
        return 'sina'
    return 'free'          # 本地文件、腾讯、baostock、mootdx …


_HITS: dict[str, deque] = {}
_LOCK = threading.Lock()
# 单次等待的上限。真被限住时宁可让这一次调用等一会儿，也不能等到跑批超时 ——
# 超过这个数就直接放行并把情况记在 message 里，让人看见"限流已经压不住了"。
MAX_WAIT = 20.0

# 阈值可以在库里改（`config.setting['rate_limit.<组>']`，值形如
# `{"min_interval":0.25,"per_min":140,"per_5min":210}`），代码里的 `LIMITS` 只是
# 兜底默认值。**带 60s 缓存**：`_gate()` 是每次取数都走的热路径，
# 全市场批量取数时一天几万次，每次查一趟库不如把阈值缓存一分钟。
_SETTING_TTL = 60.0
_setting_cache: dict[str, tuple[float, RateLimit]] = {}


def limit_of(group: str) -> RateLimit:
    """这个限流组当前生效的阈值。库里配了用库里的，否则用代码默认值。"""
    now = time.monotonic()
    hit = _setting_cache.get(group)
    if hit and now - hit[0] < _SETTING_TTL:
        return hit[1]
    # `tushare:ts_daily` 这种带接口后缀的组，阈值查基名（每接口同一套阈值）
    base = group.split(':', 1)[0]
    lim = LIMITS.get(base) or LIMITS['free']
    try:
        val = engine.scalar('SELECT value FROM config.setting WHERE key = %s',
                            (f'rate_limit.{base}',))

    except Exception:            # noqa: BLE001  库连不上时不该把取数也带死
        val = None
    if isinstance(val, dict):
        lim = RateLimit(
            min_interval=float(val.get('min_interval') or 0),
            per_min=int(val['per_min']) if val.get('per_min') else None,
            per_5min=int(val['per_5min']) if val.get('per_5min') else None)
    _setting_cache[group] = (now, lim)
    return lim


def _gate(ds_key: str) -> float:
    """
    取数前先过闸门，返回**实际等了多少秒**（0 表示没等）。

    实现是滑动窗口计数：只记时间戳，不记内容。进程内共享 —— 同一个进程里
    并发跑几个采集脚本时能互相看见；**跨进程管不到**（那要 PG 里的计数表，
    等真出现"两个跑批同时打东财"再说，现在跑批是串行的）。
    """
    group = limit_group(ds_key)
    lim = limit_of(group)

    if lim.min_interval <= 0 and not lim.per_min and not lim.per_5min:
        return 0.0
    waited = 0.0
    while True:
        with _LOCK:
            now = time.monotonic()
            q = _HITS.setdefault(group, deque())
            while q and now - q[0] > 300:
                q.popleft()
            sleep = 0.0
            if lim.min_interval and q:
                sleep = max(sleep, lim.min_interval - (now - q[-1]))
            if lim.per_min:
                recent = sum(1 for t in q if now - t <= 60)
                if recent >= lim.per_min:
                    # 等到窗口里最早那一次滑出 60s
                    oldest = next(t for t in q if now - t <= 60)
                    sleep = max(sleep, 60 - (now - oldest) + 0.01)
            if lim.per_5min and len(q) >= lim.per_5min:
                sleep = max(sleep, 300 - (now - q[0]) + 0.01)
            if sleep <= 0 or waited + sleep > MAX_WAIT:
                q.append(now)          # 放行（等够了，或等不下去了）
                return waited
        time.sleep(sleep)
        waited += sleep


# 可以在 /datasource → ② 数据源管理 里就地改的字段。
# **白名单，且只有这一份** —— 列名是唯一拼进 SQL 文本的东西（PG 不允许列名
# 当参数），所以它绝不能来自页面传来的字符串。
# 刻意不放进来的：health（探测的结论，手改会掩盖真实状态）、
# probe_spec / endpoint / auth_ref（改了要连带改探测代码，属于改代码不是改配置）。
EDITABLE: dict[str, str] = {
    'layer': 'text', 'ds_role': 'text', 'cost': 'text', 'tagline': 'text',
    'note': 'text', 'seq': 'int', 'is_active': 'bool',
    'quota_limit': 'int', 'quota_reset': 'text',
}



class DataSourceError(RuntimeError):
    """取数最终失败。带上 ds_key，页面和日志能直接定位到源。"""

    def __init__(self, ds_key: str, message: str):
        super().__init__(f'[{ds_key}] {message}')
        self.ds_key = ds_key


def list_sources(*, active_only: bool = True, role: str = 'reader') -> list[dict]:
    """
    注册表全量。**排序是「层级 → seq」** —— 页面上一眼看到的顺序就是"该先用谁"
    的顺序，而不是当初插入的顺序。
    """
    cond = 'WHERE is_active' if active_only else ''
    return engine.query(
        f"""
        SELECT ds_key, name, kind, provider, endpoint, auth_ref,
               layer, ds_role, cost, tagline,
               quota_limit, quota_used, quota_reset, health,
               last_ok_at, last_check, last_err, latency_ms, row_count,
               probe_spec, seq, is_active, note
          FROM config.datasource {cond}
         ORDER BY CASE layer WHEN 'p0' THEN 0 WHEN 'main' THEN 1
                             WHEN 'base' THEN 2 ELSE 3 END, seq, ds_key
        """, role=role)


def gaps(*, role: str = 'reader') -> list[dict]:
    """
    「已知缺口」清单：health 落在 `STICKY`（no_permission / broken）的那些源。

    **为什么它值得单列一份而不是让人去表里筛**：这两种状态不是故障 ——
    重试一万次也不会好，要做的是买权限或换源。摘要块上只写一个数字「已知缺口 7」，
    看到的人无从知道缺的是什么、影响谁、现在走的是哪条路，于是这个数字要么被当成
    "有 7 个东西坏了"（去查网络，白查），要么被无视。

    每行在注册表字段之外补两个**推出来的**字段（不是逐个 ds_key 写死的）：
      reason  为什么算缺口 —— 按 health 分档
      fix     怎么消除 —— no_permission 看 auth_ref（有密钥变量就是"买/配权限"），
              broken 只能等上游修版本
    "缺口影响什么、现在改走哪条路"照旧读 `note`：那一列在「② 数据源管理」里
    可以就地改，把它抄进代码等于把内容硬编码。

    ⚠️ `last_err` 与 `health` 可能讲两件事：6 个 `ts_*` 的 health 是上次 40203
    （积分不够）留下的结论，而当前 `last_err` 是"环境变量 FT_TUSHARE_TOKEN 未设置"
    —— token 没配上之前，权限那条结论无法复验。这一点在 `probe_note` 里直说，
    免得看的人以为"填个 token 就有权限了"。
    """
    out = []
    for ds in list_sources(role=role):
        if ds['health'] not in STICKY:
            continue
        err = ds.get('last_err') or ''
        if ds['health'] == BROKEN:
            ds['reason'] = '接口自身 bug（上游库/接口返回就是错的）'
            ds['fix'] = '等上游修版本；替代路径见备注'
        elif ds.get('auth_ref'):
            ds['reason'] = '接口无权限（付费档位不够，如 Tushare 40203）'
            ds['fix'] = f'升级权限或配 {ds["auth_ref"]}'
        else:
            ds['reason'] = '接口无权限'
            ds['fix'] = '需要开通权限'
        ds['probe_note'] = (
            '当前探测报的是密钥未配置，"无权限"是上次探测的结论 —— '
            'token 配上之前无法复验'
            if ds['health'] == NO_PERMISSION and '未设置' in err else '')
        out.append(ds)
    return out


def update_field(ds_key: str, column: str, value) -> None:
    """
    改注册表里的一个字段（给「数据源管理」那张可编辑表用）。

    `column` 必须在 `EDITABLE` 白名单里 —— 它是**唯一**拼进 SQL 文本的东西
    （PG 不允许列名当参数），所以不能接受页面传来的任意字符串。
    值本身照旧走参数化。

    空串一律当 NULL：表格里把一格删空，意思是"没有"，不是"值是空字符串"。
    """
    kind = EDITABLE.get(column)
    if kind is None:
        raise ValueError(f'{column} 不允许在页面上改')
    v: object = None if value in (None, '') else value
    if kind == 'int' and v is not None:
        try:
            v = int(str(v).strip())
        except ValueError:
            raise ValueError(f'{column} 要填整数，收到「{value}」') from None
    if kind == 'bool':
        v = bool(v) if isinstance(v, bool) else str(value).strip() in (
            '是', 'true', 'True', '1', 'y', 'Y')
    if column == 'layer' and v not in LAYERS:
        raise ValueError(f'层级只能是 {"/".join(LAYERS)}，收到「{value}」')
    if column in ('seq', 'is_active') and v is None:
        raise ValueError(f'{column} 不能留空')
    engine.execute(
        f'UPDATE config.datasource SET {column} = %s, updated_at = now() '
        'WHERE ds_key = %s', (v, ds_key), role='app')



def get(ds_key: str, *, role: str = 'reader') -> dict | None:
    return engine.query_one(
        'SELECT * FROM config.datasource WHERE ds_key = %s', (ds_key,), role=role)


def health_of(ds_key: str, *, role: str = 'reader') -> str:
    return engine.scalar(
        'SELECT health FROM config.datasource WHERE ds_key = %s',
        (ds_key,), role=role) or 'unknown'


def record(ds_key: str, health: str, *, latency_ms: int | None = None,
           row_count: int | None = None, message: str | None = None,
           quota_used: int | None = None) -> None:
    """
    写回一次调用/探测的结果。

    **既定事实不覆盖**：已标为 no_permission / broken 的源，即便某次调用碰巧
    抛了个网络错，也不改成 down —— 那会把"没权限"伪装成"网络抖动"，
    让人去修错的东西。真要改，人去 /datasource 页面上手动改。
    """
    cur = health_of(ds_key)
    keep = cur in STICKY and health != cur
    engine.execute(
        """
        UPDATE config.datasource
           SET health     = CASE WHEN %(keep)s THEN health ELSE %(health)s END,
               last_check = now(),
               last_ok_at = CASE WHEN %(health)s = 'ok' THEN now() ELSE last_ok_at END,
               last_err   = CASE WHEN %(health)s = 'ok' THEN NULL
                                 ELSE left(coalesce(%(msg)s, last_err), 500) END,
               latency_ms = coalesce(%(lat)s, latency_ms),
               row_count  = coalesce(%(rows)s, row_count),
               quota_used = coalesce(%(quota)s, quota_used),
               updated_at = now()
         WHERE ds_key = %(key)s
        """,
        {'key': ds_key, 'health': health, 'keep': keep, 'msg': message,
         'lat': latency_ms, 'rows': row_count, 'quota': quota_used},
        role='admin')
    engine.execute(
        """
        INSERT INTO ops.datasource_probe (ds_key, health, latency_ms, row_count, message)
        VALUES (%s, %s, %s, %s, left(%s, 500))
        """, (ds_key, health, latency_ms, row_count, message), role='admin')


def call(ds_key: str, fn: Callable[..., Any], *args, retry: int = RETRY,
         required: bool = True, **kwargs) -> Any:
    """
    带重试地调用一个外部接口，并把健康状态写回注册表。

    `required=False` 时最终失败返回 None 而不抛 —— 用于"有则更好"的分量
    （比如概念热度的资金流向），少一个分量应当降级而不是让整批评分失败。

    重试次数用指数退避：东财实测是**间歇性**断连，隔一两秒往往就通了；
    但连续 3 次都失败就该记成 degraded 让人看见，而不是无限重试把跑批拖死。

    **每次尝试前先过限流闸门**（`_gate`）：东财有风控会封 IP，重试撞上风控
    只会把封禁时间拉长。等待时长会算进 `latency_ms` 并在 message 里写明 ——
    否则"这个源变慢了"和"我们自己在等闸门"看起来一样。
    """
    last = None
    t0 = time.perf_counter()
    waited = 0.0
    for i in range(max(1, retry)):
        waited += _gate(ds_key)
        try:
            out = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i < retry - 1:
                time.sleep(BACKOFF ** (i + 1))
            continue
        ms = int((time.perf_counter() - t0) * 1000)
        n = len(out) if hasattr(out, '__len__') else None
        # 第一次就成功才算 ok；重试过说明这个源在抖，标 degraded 让人看见
        note = None if i == 0 else f'第 {i + 1} 次尝试才成功'
        if waited >= 1.0:
            note = (note + '；' if note else '') + f'限流等待 {waited:.1f}s'
        record(ds_key, OK if i == 0 else DEGRADED, latency_ms=ms, row_count=n,
               message=note)
        return out

    ms = int((time.perf_counter() - t0) * 1000)
    msg = f'{type(last).__name__}: {last}'
    if waited >= 1.0:
        msg += f'（其中限流等待 {waited:.1f}s）'
    record(ds_key, DEGRADED if retry > 1 else DOWN, latency_ms=ms, message=msg)

    if required:
        raise DataSourceError(ds_key, f'重试 {retry} 次仍失败：{msg}')
    return None
