"""`/api/mcp/*` 的数据底：登记表、连接自检、试调用。

三条口径（台账见 docs/MCP服务登记表.md）：
1. 这里**不复制**第二份清单，一律现读 `mcp_service.capabilities`；条数由接口现算，界面不抄。
2. 自检项取不到就回报 `available=false` + 原因，**不许兜成 ✓**——那会让「MCP 服务」这一屏
   变成一句假的"都通了"。
3. 试调用只走登记表白名单里的只读端点，参数逐条对回端点真认识的键；不做重算、不接路径参数。
"""
from __future__ import annotations

import json
import os
import socket
import sys
from typing import Any

from mcp_service import capabilities as caps
from mcp_service import client


def _sdk_version() -> str:
    try:
        from importlib.metadata import version

        return version("mcp")
    except Exception as exc:  # noqa: BLE001
        return f"读不出：{type(exc).__name__}"


def _port_listening(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _backend_probe() -> dict[str, Any]:
    """8910 在不在跑。工具全靠它，所以这一项坏了要最响。"""
    import httpx

    url = f"{client.base_url()}/api/health"
    try:
        resp = httpx.get(url, headers=client.request_headers(), timeout=5)
    except httpx.HTTPError as exc:
        return {"available": False, "url": client.base_url(),
                "reason": f"后端不可达：{type(exc).__name__}: {str(exc)[:140]}"}
    return {"available": resp.status_code == 200, "url": client.base_url(),
            "status_code": resp.status_code,
            "reason": "" if resp.status_code == 200 else f"HTTP {resp.status_code}：{resp.text[:120]}"}


def _snippets() -> dict[str, Any]:
    """两段可直接粘进智能体配置的 JSON（路径用当前这台机器的真实解释器）。

    ⚠️ 必须带 `PYTHONPATH`：`-m mcp_service` 靠工作目录找到包，而宿主（Claude Code / codex /
    Qoder 的 MCP 设置）多半不给你设 cwd —— 少了这一条就是 `ModuleNotFoundError: mcp_service`，
    报出来还像"服务没装"。`cwd` 也一并给出，供支持它的宿主用。
    """
    port = int(os.environ.get("YUANSHAN_MCP_HTTP_PORT", "8920"))
    env = {"YUANSHAN_MCP_BASE": client.base_url(), "PYTHONPATH": str(caps.ROOT)}
    if os.environ.get("VR_API_KEY"):
        env["VR_API_KEY"] = "<与后端同一个 VR_API_KEY>"
    return {
        "stdio": {"command": sys.executable, "args": ["-m", "mcp_service"],
                  "cwd": str(caps.ROOT), "env": env},
        "http": {"type": "streamable-http", "url": f"http://{_http_host()}:{port}/mcp"},
        "http_start_command": " ".join([f'"{sys.executable}"', "-m", "mcp_service",
                                        "--transport", "streamable-http",
                                        "--host", _http_host(), "--port", str(port)]),
        "cwd": str(caps.ROOT),
    }


def _http_host() -> str:
    return os.environ.get("YUANSHAN_MCP_HTTP_HOST", "127.0.0.1")


def status_payload() -> dict[str, Any]:
    registry = caps.registry_payload()
    backend = _backend_probe()
    port = int(os.environ.get("YUANSHAN_MCP_HTTP_PORT", "8920"))
    http_up = _port_listening(_http_host(), port)
    checks = [
        {"key": "backend", "label": "后端 8910 可达（工具全部经它取数）",
         "available": backend["available"], "reason": backend["reason"], "detail": backend["url"]},
        {"key": "registry", "label": "登记表可读", "available": bool(registry["capabilities"]),
         "reason": "", "detail": f"A {registry['counts']['A']} / B {registry['counts']['B']} / "
                                 f"C {registry['counts']['C']} / 索引 {registry['counts']['index']}"},
        {"key": "doc", "label": "台账与登记表逐条对齐", "available": bool(registry["doc"]["ok"]),
         "reason": "" if registry["doc"]["ok"] else json.dumps(
             {k: v for k, v in registry["doc"].items() if k in
              ("missing_in_doc", "extra_in_doc", "path_mismatch")}, ensure_ascii=False),
         "detail": registry["doc"]["doc"]},
        {"key": "sdk", "label": "MCP SDK 已安装", "available": _sdk_version().count(".") > 0,
         "reason": "" if _sdk_version().count(".") > 0 else f"读不出版本：{_sdk_version()}",
         "detail": f"mcp {_sdk_version()}"},
        {"key": "http_mode", "label": f"streamable HTTP 端口 {port} 在听",
         "available": http_up,
         "reason": "" if http_up else f"{_http_host()}:{port} 没人听——需要先跑下面那条启动命令",
         "detail": "常驻或局域网接入才需要；本机智能体用 stdio 那一段就行"},
    ]
    essential = {"backend", "registry", "doc", "sdk"}
    failing = [c["key"] for c in checks if not c["available"]]
    return {
        # `available` 只回答「这份体检取到了没有」。可选项（HTTP 模式没起）不算整屏不可用——
        # 否则 `hasData` 会判成「未覆盖」，把明明在手里的四条绿项一起藏掉。
        "available": True,
        "all_green": not failing,
        "blocking": [k for k in failing if k in essential],
        "optional_pending": [k for k in failing if k not in essential],
        "service_name": registry["service_name"],
        "counts": registry["counts"],
        "tools": registry["tools"],
        "checks": checks,
        "config": _snippets(),
        "redlines": [
            "只读：只注册 GET/HEAD 端点，写类端点不进目录、兜底工具也拒绝",
            "不触发计算：refresh 一类参数在 MCP 侧一律不外露、不允许传",
            "失败不兜空：后端不可达或端点报错就抛异常，不返回空表冒充「今天没有」",
        ],
        "generated_reason": "" if backend["available"] else backend["reason"],
    }


def registry_payload() -> dict[str, Any]:
    return caps.registry_payload()


def try_invoke(capability_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """按登记表白名单真的调一次，原样返回载荷 + 新鲜度回执。"""
    try:
        payload = client.call_capability(capability_id, params or {})
        entry = None
        if capability_id in caps.CURATED_BY_ID:
            entry = caps.capability_entry(caps.CURATED_BY_ID[capability_id])
        return {"available": True, "capability_id": capability_id,
                "label": entry["label"] if entry else capability_id,
                "handler": entry["handler"] if entry else "",
                "tables": entry["tables"] if entry else [],
                "result": payload}
    except (client.McpDataError, KeyError) as exc:
        return {"available": False, "capability_id": capability_id,
                "reason": f"{type(exc).__name__}: {exc}", "result": None}
    except Exception as exc:  # noqa: BLE001 取数链路上任何异常都要如实报，不能当"没有数据"
        return {"available": False, "capability_id": capability_id,
                "reason": f"试调用失败：{type(exc).__name__}: {str(exc)[:200]}", "result": None}
