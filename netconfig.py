"""局域网访问配置：绑定地址与来源白名单的**唯一来源**。

为什么要单独一个文件、还只用标准库：`scripts/manage.py` 在**依赖还没装**的时候就要知道
该用哪个 `--host` 起 uvicorn（那一步正是"装不装得上"之前的事）。所以这里不能 import
`duanxian`（它会拉起 pandas/psycopg 那一串），也不能等到 FastAPI 起来才判。

配置落在业务数据根下（与 `jobs/config.json` 同一套规矩，见
`docs/系统配置工作指南/03-环境变量与作业配置.md`），**不落 PG**：
绑定地址必须在连库之前就能读到，而且换机器/删库都不该把这个一起丢掉。

    <ASTOCK_DATA_HOME>/net/config.json
    {"lan_enabled": false, "allowlist": ["192.168.186.7"]}

三条硬规矩（错了会静默地把这台机器暴露到局域网）：

1. **改这个配置 = 改网络边界，只允许从回环改。** 白名单里那台机器如果能通过
   `POST /api/net` 给自己或别人加白，这道闸就形同没有。
2. **绑定地址在启动时决定，运行中改不了**（socket 已经绑在哪个地址就是哪个）。
   所以页面勾选后必须重启才生效，`running_matches_config()` 专管把这件事说清楚。
3. **回环永远放行、也永远不需要写进白名单**；`0.0.0.0`、`127.x`、广播地址一律拒绝。
"""
from __future__ import annotations

import ipaddress
import json
import os
from datetime import datetime
import socket
import sys
import threading
import time
from pathlib import Path

CONFIG_FILE = "net/config.json"
MAX_ALLOWLIST = 64          # 白名单是逐台机器登记的，不是网段；上限防手滑粘一大串
LOOPBACK_PREFIX = "127."
_write_lock = threading.Lock()


def dt_now() -> str:
    """盖章用的墙钟时刻（带本地时区偏移，上海这台就是 +08:00 的本地时间）。"""
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def config_path() -> str:
    """配置文件位置。`VIBE_NET_CONFIG` 是给测试注入用的，日常不要设。"""
    override = os.environ.get("VIBE_NET_CONFIG", "").strip()
    if override:
        return override
    root = os.environ.get("ASTOCK_DATA_HOME") or str(Path.home() / ".duanxian-agents")
    return str(Path(root) / CONFIG_FILE)


def defaults() -> dict:
    return {"lan_enabled": False, "allowlist": []}


def load() -> dict:
    """读配置。**读不到/坏了一律按"只开回环"** —— 网络边界上不能拿"未知"当"允许"。

    坏文件必须出声：静默回落会让用户以为"我勾了但软件不认"，
    而实际情况是文件写坏了，两种说法对应的动作完全不同。
    """
    path = config_path()
    out = defaults()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return out
    except (OSError, ValueError) as exc:
        out["_warning"] = f"配置文件读不了（{type(exc).__name__}），按「只监听回环」运行：{path}"
        return out
    if not isinstance(raw, dict):
        out["_warning"] = f"配置内容不是对象（{type(raw).__name__}），按「只监听回环」运行：{path}"
        return out
    out["lan_enabled"] = bool(raw.get("lan_enabled", False))
    listed = raw.get("allowlist")
    if isinstance(listed, list):
        good, bad = [], []
        for item in listed:
            why = validate_ip(item)
            (bad if why else good).append(item if isinstance(item, str) else repr(item))
        out["allowlist"] = sorted(set(good))
        if bad:
            out["_warning"] = (f"白名单里有 {len(bad)} 项不是合法 IPv4，已忽略：{'、'.join(map(str, bad[:5]))}"
                               "（只支持单个地址，不支持网段/CIDR）")
    elif listed is not None:
        out["_warning"] = "白名单不是数组，已按空处理"
    return out


def validate_ip(value) -> str | None:
    """返回 None 表示可以进白名单，否则一句人话说明为什么不行。"""
    if not isinstance(value, str) or not value.strip():
        return "白名单项必须是非空字符串（IPv4 点分十进制）"
    text = value.strip()
    try:
        ip = ipaddress.IPv4Address(text)
    except ValueError:
        return f"`{text}` 不是合法 IPv4 地址（不支持 IPv6，也不支持网段/CIDR/主机名）"
    if ip.is_unspecified:
        return "`0.0.0.0` 不是来源地址，它是绑定地址 —— 白名单里不许出现（等于全开）"
    if ip.is_loopback:
        return f"`{text}` 是本机回环，本来就永久放行，不用写进白名单"
    if ip == ipaddress.IPv4Address("255.255.255.255"):
        return "`255.255.255.255` 是广播地址，不是某一台机器"
    return None


def save(*, lan_enabled: bool | None = None, allowlist: list | None = None,
         via: str = "") -> dict:
    """整份覆盖写入（原子），并**盖章：谁、什么时候**。

    白名单每多一条就多一台能看到本机全部数据的机器。09-28 那天名单上悄悄多出两个地址
    （其中一个还是本机自己的局域网 IP），事后没人说得清是谁加的、为什么加 ——
    与作业开关被关三次是同一类问题，所以同一套留痕。
    """
    current = load()
    nxt = {"lan_enabled": current["lan_enabled"] if lan_enabled is None else bool(lan_enabled),
           "allowlist": current["allowlist"] if allowlist is None else list(allowlist),
           "_changed_at": dt_now(),
           "_changed_by": via or "未知调用方"}
    path = config_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = path + ".tmp"
    with _write_lock:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(nxt, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    return {k: v for k, v in nxt.items() if not k.startswith("_")}


def add_ip(value: str, via: str = "") -> tuple[dict, str | None]:
    """加一条白名单。返回 `(新配置, 拒绝原因或 None)` —— 拒绝必须带原因，不能只回 400。"""
    current = load()
    why = validate_ip(value)
    if why:
        return current, why
    ip = str(ipaddress.IPv4Address(value.strip()))
    if ip in current["allowlist"]:
        return current, f"{ip} 已经在白名单里"
    if len(current["allowlist"]) >= MAX_ALLOWLIST:
        return current, f"白名单最多 {MAX_ALLOWLIST} 条，先删再加"
    return save(allowlist=current["allowlist"] + [ip], via=via or "add_ip"), None


def remove_ip(value: str, via: str = "") -> tuple[dict, str | None]:
    current = load()
    ip = str(value).strip()
    if ip not in current["allowlist"]:
        return current, f"{ip} 不在白名单里"
    return save(allowlist=[x for x in current["allowlist"] if x != ip],
                via=via or "remove_ip"), None


def _raw_stamp(key: str):
    """留痕字段不在 `load()` 的输出里（它是给人读的配置视图），单独取。"""
    try:
        raw = json.loads(Path(config_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw.get(key) if isinstance(raw, dict) else None


def bind_host(cfg: dict | None = None) -> str:
    """启动时该绑哪个地址。**运行中改不了**，所以这就是"局域网到底开没开"的事实。"""
    return "0.0.0.0" if (cfg or load())["lan_enabled"] else "127.0.0.1"


# starlette/uvicorn 的 TestClient 默认对端名。生产上 `request.client.host` 永远是真 IP
# （内核给的），只有测试夹具才会给出这个串 —— 不认它，全项目那批用 TestClient 的接口测试
# 会一起变 403，而那看起来像"局域网功能把站点弄坏了"。
# 这不是放行口子：对面写不了内核的对端地址。
TEST_HARNESS_CLIENTS = {"testclient"}


def is_allowed_client(host: str | None, cfg: dict | None = None) -> tuple[bool, str]:
    """这个来源地址能不能访问。返回 `(放行?, 原因)`。

    只认 `request.client.host`（内核给的对端地址），**不看 `X-Forwarded-For` 这类头** ——
    那是客户端自己写的，信它等于把白名单交给对面伪造。本机没有任何反向代理。
    """
    if not host:
        return False, "读不到来源地址（连接信息缺失）—— 按不放行处理"
    text = str(host).strip()
    bare = text.split("%")[0]                      # IPv6  zone id（fe80::1%eth0）
    if bare.lower() in TEST_HARNESS_CLIENTS:
        return True, "测试夹具对端（TestClient 默认名，不是真实地址）"
    if bare == "::1" or bare.startswith(LOOPBACK_PREFIX):
        return True, "本机回环"
    # 一次请求只读一遍配置（局域网浏览一轮要发几十个请求，每个读两次文件纯属浪费）
    cfg = cfg or load()
    if not cfg["lan_enabled"]:
        return False, "未开放局域网访问（当前只允许本机 127.0.0.1 访问）"
    try:
        ip = str(ipaddress.IPv4Address(bare))
    except ValueError:
        return False, f"来源 `{bare}` 不是 IPv4，白名单只按 IPv4 逐条匹配"
    return (True, "白名单内") if ip in cfg["allowlist"] else (False, f"来源 {ip} 不在白名单内")


def local_ipv4s() -> list[str]:
    """本机的非回环 IPv4 —— 给人看"局域网里用哪个地址访问我"。

    读不到就交空表并说明，**不猜**：报一个不对的地址比不报更难查。
    """
    out: list[str] = []
    try:
        info = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        out = sorted({s[4][0] for s in info})
    except OSError:
        pass
    probe = ""
    try:                                        # 不发包：UDP connect 只让内核挑默认出口地址
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 53))
            probe = s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        probe = ""
    for ip in ([probe] if probe else []) + out:
        if ip and not ip.startswith(LOOPBACK_PREFIX) and ip not in out:
            out.append(ip)
    return sorted({ip for ip in out if not ip.startswith("169.254.")})


_LOCAL_CACHE: tuple[float, list[str]] = (0.0, [])


def local_ips(ttl: float = 30.0) -> list[str]:
    """本机地址的带 TTL 缓存版 —— 每个请求都去 `getaddrinfo` 会把闸变成减速带。

    缓存过期后**刷新失败要保留上一次的名单**，不是清空：网卡抖一下、DNS 一时不通，
    结果不该是"已经在用局域网访问的对面突然 403"。
    """
    global _LOCAL_CACHE
    now = time.monotonic()
    if _LOCAL_CACHE[1] and now - _LOCAL_CACHE[0] < ttl:
        return _LOCAL_CACHE[1]
    try:
        found = local_ipv4s()
    except Exception:  # noqa: BLE001
        found = []
    if found:
        _LOCAL_CACHE = (now, found)
        return found
    return _LOCAL_CACHE[1]


def is_local_host(name: str | None) -> bool:
    """Host/Origin 里的这台机器自己的地址。开放局域网时对面敲的就是它。"""
    if not name:
        return False
    return name.lower() in {ip.lower() for ip in local_ips()}


def running_pid() -> int | None:
    return os.getpid()


# ---------------------------------------------------------------- 被挡记录
# 放在这一层而不是 `server.py` 里，是因为 `/api/net`（在 vr/ 下）要读它，
# 而 vr 反过来 import server 会成环。
# 为什么要记：白名单最难查的不是"加错了"，而是"对面到底以哪个 IP 进来" ——
# 中间过一层 NAT 时，你心里那个地址和对端实际地址不是一回事。
_DENIALS: list[dict] = []
_DENIAL_TOTAL = 0
_denial_lock = threading.Lock()


def note_denial(ip: str | None, why: str, path: str = "", stamp: str = "") -> None:
    global _DENIAL_TOTAL
    with _denial_lock:
        _DENIALS.append({"client": ip or "未知", "reason": why, "path": path, "at": stamp})
        del _DENIALS[:-20]              # 只留最近 20 条；总数不丢
        _DENIAL_TOTAL += 1


def denials() -> dict:
    with _denial_lock:
        return {"recent": list(_DENIALS), "total": _DENIAL_TOTAL}


def running_bind(argv: list[str] | None = None) -> dict:
    """本进程**实际**绑在哪个地址/端口 —— 从启动参数读，不看配置。

    必须是"实际"而不是"配置说"：socket 已经绑定了，改配置不会让它改绑。
    页面据此才能说出「已保存，但要重启才生效」，而不是让人以为勾上就通了。

    ⚠️ **只在服务自己那个进程里调用才有意义**：读的是本进程的 argv。
    从 `python -c` 或任何 CLI 里调，argv 里没有 `--host`，会一律报"绑在 127.0.0.1"
    —— 那不是现场事实，只是这条命令自己的样子。
    """
    args = list(argv if argv is not None else __import__("sys").argv)
    host, port = "127.0.0.1", int(os.environ.get("VIBE_PORT") or 8910)
    for i, item in enumerate(args):
        token = str(item)
        if token in {"--host", "-h"} and i + 1 < len(args):
            host = str(args[i + 1])
        elif token.startswith("--host="):
            host = token.split("=", 1)[1]
        elif token in {"--port", "-p"} and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                pass
        elif token.startswith("--port="):
            try:
                port = int(token.split("=", 1)[1])
            except ValueError:
                pass
    return {"host": host, "port": port, "lan_open": host in {"0.0.0.0", "::"}}


def state(cfg: dict | None = None) -> dict:
    """给 `/api/net` 与系统配置页的一揽子读数：配置说的 vs 进程实际做的。"""
    cfg = cfg or load()
    bound = running_bind()
    want = bind_host(cfg)
    return {
        "lan_enabled": bool(cfg["lan_enabled"]),
        "allowlist": list(cfg["allowlist"]),
        "config_path": config_path(),
        "config_warning": cfg.get("_warning"),
        # 白名单每多一条就多一台能看到本机全部数据的机器，所以"谁加的、什么时候"必须看得见
        "changed_at": _raw_stamp("_changed_at"), "changed_by": _raw_stamp("_changed_by"),
        "running_host": bound["host"],
        "running_port": bound["port"],
        "running_lan_open": bound["lan_open"],
        "should_bind": want,
        # 只说"要不要重启"，不假装能热切换：绑定地址是启动那一刻定死的
        "needs_restart": want != bound["host"],
        "lan_urls": ([f"http://{ip}:{bound['port']}" for ip in local_ipv4s()]
                     if bound["lan_open"] else []),
        "loopback_url": f"http://127.0.0.1:{bound['port']}",
        "note": ("本机回环 127.0.0.1 永久放行；局域网来源必须逐条命中白名单，"
                 "匹配的是内核给的对端地址，不看 X-Forwarded-For 这类可伪造头。"),
    }
