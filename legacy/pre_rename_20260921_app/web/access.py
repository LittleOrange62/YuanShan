# -*- coding: utf-8 -*-
"""
访问闸门：来源 IP 白名单。

**为什么要有这一层**：本站没有登录门（`config.app_user` 空的时候
`auth.login_required()` 返回 False），而 `.env` 里的 `FT_WEB_HOST` 一直是
`0.0.0.0`。以前靠 `run.py` 把绑定地址强行改回 `127.0.0.1` 来兜底 —— 这等于
「要么只有本机能用，要么全网段裸奔」，没有中间档。这里补上中间档：

    默认只放行本机（loopback），其余一律 403；
    只有白名单里的地址（`config.setting.web_allowlist`）才能访问这个端口。

设计上踩过的几个点，都写在这里免得后人改坏：

- **loopback 永久放行，不受白名单影响**。否则白名单填错一个字，本机也进不来，
  唯一的修复入口（网页）自己把自己锁死，只能去改库。
- **只信 `request.remote_addr`**，绝不看 `X-Forwarded-For`。本机没有反向代理，
  信了那个头等于白名单可以被请求方自己伪造 —— 那这层闸门就是装饰。
- **失败方向选安全那一边**：白名单读不出来（库连不上、值写坏了）时只放行
  loopback，而不是放行全部。
- 白名单带 TTL 缓存（`CACHE_TTL_S`），但网页保存后会立刻 `invalidate()`：
  每个请求查一次库太贵（一个页面几十个请求），改完要等几秒生效也不行。

条目支持单个 IP 与 CIDR 网段：`192.168.176.67`、`192.168.176.0/24`、IPv6 同理。
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import time

from app.db.repo import registry as reg_repo

#: `config.setting` 里的两个键
ALLOWLIST_KEY = 'web_allowlist'
BIND_KEY = 'web_bind_host'

#: 默认白名单。用户指定的那台机器；loopback 不用写在这里（永久放行）。
DEFAULT_ALLOWLIST: tuple[str, ...] = ('192.168.176.67',)

#: 默认绑定地址：只绑本机。要让白名单里的局域网地址真能连上，得改成 0.0.0.0。
DEFAULT_BIND = '127.0.0.1'

CACHE_TTL_S = 3.0

_cache: tuple[float, tuple[str, ...]] | None = None

DENY_BODY = (
    '403 Forbidden\n\n'
    'FinanceTool 只允许白名单内的地址访问。\n'
    '你的来源地址：%s\n\n'
    '要放行它：在本机打开 http://127.0.0.1:%s/admin → 「访问白名单」把这个地址加进去。\n'
)


def parse_entry(text: str):
    """
    把一条白名单文本解析成网段对象。看不懂就抛 ValueError，**不猜**。

    `strict=False` 是故意的：`192.168.176.67/24` 这种主机位没清零的写法很常见，
    直接当 `192.168.176.0/24` 收下，比报错让人反复试更好用。
    """
    s = (text or '').strip()
    if not s:
        raise ValueError('空条目')
    try:
        return ipaddress.ip_network(s, strict=False)
    except ValueError as exc:
        raise ValueError(f'{s} 不是合法的 IP 或网段（{exc}）') from None


def normalize(items) -> list[str]:
    """校验并规范化一串条目；顺序保持，重复去掉。"""
    if isinstance(items, str):
        items = items.replace('，', ',').replace(';', ',').replace('\r', '\n')
        items = [x for line in items.split('\n') for x in line.split(',')]
    out: list[str] = []
    for raw in items or []:
        s = str(raw).strip()
        if not s or s.startswith('#'):
            continue
        net = parse_entry(s)
        # 单个地址存成裸 IP（`/32` 看着像网段，容易误读）
        text = str(net.network_address) if net.num_addresses == 1 else str(net)
        if text not in out:
            out.append(text)
    return out


def invalidate() -> None:
    global _cache
    _cache = None


def allowlist(*, fresh: bool = False) -> list[str]:
    """当前白名单（不含 loopback）。读不出来时回落到默认值。"""
    global _cache
    now = time.monotonic()
    if not fresh and _cache is not None and now - _cache[0] < CACHE_TTL_S:
        return list(_cache[1])
    try:
        raw = reg_repo.setting(ALLOWLIST_KEY, list(DEFAULT_ALLOWLIST))
        items = normalize(raw)
    except Exception:  # noqa: BLE001
        # 库连不上就不缓存，下个请求再试；这一轮只有 loopback 能进
        return []
    _cache = (now, tuple(items))
    return items


def networks() -> list:
    out = []
    for s in allowlist():
        try:
            out.append(parse_entry(s))
        except ValueError:
            continue
    return out


def save_allowlist(items) -> list[str]:
    """写回 `config.setting` 并让缓存立刻失效。返回落库后的清单。"""
    clean = normalize(items)
    reg_repo.set_setting(ALLOWLIST_KEY, clean,
                         label='Web 访问白名单（IP 或 CIDR，loopback 永久放行）',
                         category='security')
    invalidate()
    return clean


def bind_host() -> str:
    """监听地址。只绑本机时白名单里的局域网地址根本连不上 —— 页面上会讲清楚。"""
    try:
        v = reg_repo.setting(BIND_KEY, DEFAULT_BIND)
    except Exception:  # noqa: BLE001
        return DEFAULT_BIND
    v = str(v or '').strip()
    return v or DEFAULT_BIND


def save_bind_host(host: str) -> str:
    h = (host or '').strip()
    if h not in ('127.0.0.1', '0.0.0.0'):
        raise ValueError('监听地址只能是 127.0.0.1（仅本机）或 0.0.0.0（本机 + 局域网）')
    reg_repo.set_setting(BIND_KEY, h, label='Web 监听地址', category='security')
    return h


# 进程**实际**绑的地址。由 run.py 在 serve() 之前登记；库里的设置改了以后，
# 页面要能区分"已保存"和"已生效"—— 只看设置的话，改完立刻显示"局域网可达"，
# 而实际上还绑在 127.0.0.1 上，别人照样连不上，然后就会去怀疑白名单。
_running_bind: str | None = None


def note_running_bind(host: str) -> None:
    global _running_bind
    _running_bind = host


def bind_host_running() -> str | None:
    return _running_bind



def allowed(remote_addr: str | None) -> bool:
    if not remote_addr:
        return False
    try:
        ip = ipaddress.ip_address(remote_addr.split('%')[0])
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    # IPv6 映射过来的 IPv4（`::ffff:192.168.1.2`）要还原成 v4 再比，
    # 否则白名单里写的 v4 网段永远匹配不上。
    mapped = getattr(ip, 'ipv4_mapped', None)
    if mapped is not None:
        if mapped.is_loopback:
            return True
        ip = mapped
    return any(ip.version == net.version and ip in net for net in networks())


def install(flask_app, *, port: int | str = '') -> None:
    """在 Flask 上挂 `before_request` 闸门。Dash 的静态资源也走 Flask，一并覆盖。"""
    from flask import request

    @flask_app.before_request
    def _ip_gate():  # noqa: ANN202
        ip = request.remote_addr
        if allowed(ip):
            return None
        # 拒绝要留痕：不打日志的话，"同事说打不开"就只能靠猜
        stamp = dt.datetime.now().strftime('%H:%M:%S')
        print(f'[{stamp}] 拒绝访问：{ip} → {request.path}（不在白名单里）',
              flush=True)
        return (DENY_BODY % (ip, port or ''), 403,
                {'Content-Type': 'text/plain; charset=utf-8'})
