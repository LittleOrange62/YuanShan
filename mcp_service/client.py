"""MCP 侧唯一的取数出口：只读调用本机后端，异常如实抛出，绝不兜成空值。

工具拿到的数＝屏幕上那一格的数——因为这里调的就是页面在用那个端点，不重算、不缓存、不改形。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from .capabilities import (CURATED_BY_ID, capability_entry, endpoint_index)

DEFAULT_BASE = "http://127.0.0.1:8910"


class McpDataError(RuntimeError):
    """取数失败。工具必须把它抛出去，而不是返回空表——空值会被下游写成一句合理的假读数。"""


def base_url() -> str:
    return (os.environ.get("YUANSHAN_MCP_BASE") or DEFAULT_BASE).rstrip("/")


def request_headers() -> dict[str, str]:
    key = (os.environ.get("VR_API_KEY") or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _coerce(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """只放行该端点真认识的查询键，并按 schema 收界。

    FastAPI 对不认识的键会直接丢掉——「筛了个寂寞」就是这么来的，所以在这一层就拒。
    """
    entry = endpoint_index().get(path)
    if entry is None:
        raise McpDataError(f"端点不在只读索引里：{path}")
    if entry["excluded"]:
        raise McpDataError(f"{path} 被显式排除（{entry['excluded']}）")
    if entry["templated"]:
        raise McpDataError(f"{path} 含路径参数，请用专用工具或 capability_id")

    known = {p["name"]: p for p in entry["params"]}
    sent: dict[str, Any] = {}
    for name, value in (params or {}).items():
        if name in ("refresh",):
            raise McpDataError(f"{path} 的 refresh 参数不允许从 MCP 侧传（会触发重算并抢 8910 的互斥）")
        if name not in known:
            raise McpDataError(f"{path} 不认识参数 {name}；可用参数：{sorted(known)}")
        spec = known[name]
        bounds = spec["bounds"]
        if isinstance(value, bool):
            sent[name] = "true" if value else "false"
            continue
        if spec["type"] in ("integer", "number") and value != "" and value is not None:
            num = float(value)
            if "ge" in bounds and num < float(bounds["ge"]):
                raise McpDataError(f"{name}={value} 低于下限 {bounds['ge']}")
            if "le" in bounds and num > float(bounds["le"]):
                raise McpDataError(f"{name}={value} 超过上限 {bounds['le']}")
        sent[name] = value
    missing = [n for n, p in known.items() if p["required"] and n not in sent]
    if missing:
        raise McpDataError(f"{path} 缺必填参数：{missing}")
    return sent


def fetch(path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
    """GET 一个只读端点，返回 `{"data": 屏幕那份载荷, "_meta": 新鲜度回执}`。"""
    import httpx

    query = _coerce(path, params or {})
    url = f"{base_url()}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    started = datetime.now()
    try:
        resp = httpx.get(url, headers=request_headers(), timeout=timeout or float(
            os.environ.get("YUANSHAN_MCP_TIMEOUT", "60")))
    except httpx.HTTPError as exc:
        raise McpDataError(
            f"后端不可达：{url}（{type(exc).__name__}: {str(exc)[:120]}）。"
            f"先确认 8910 在跑，或用 YUANSHAN_MCP_BASE 指向实际地址") from exc
    if resp.status_code != 200:
        raise McpDataError(f"{path} 返回 HTTP {resp.status_code}：{resp.text[:200]}")
    try:
        body = resp.json()
    except json.JSONDecodeError as exc:
        raise McpDataError(f"{path} 返回的不是 JSON：{resp.text[:160]}") from exc

    data = body.get("data", body) if isinstance(body, dict) else body
    payload = {"data": data, "_meta": {
        "source_path": path,
        "as_of": _as_of(data),
        "fetched_at": started.isoformat(timespec="seconds"),
        "elapsed_ms": int((datetime.now() - started).total_seconds() * 1000),
        "row_count": _row_count(data),
        "degraded": _degraded(data),
    }}
    return payload


def _as_of(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("date", "as_of", "base_date", "trade_date", "captured_at"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _row_count(data: Any) -> int | None:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("rows", "items", "stocks", "sectors", "results", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
    return None


def _degraded(data: Any) -> list[str]:
    """把载荷里自报的「哪几族列没回填」收进回执，让智能体看得见缺数而不是以为数就是 0。

    只留 code＋label：正文长（几百字），全量塞进回执会把智能体的上下文吃掉。
    """
    if not isinstance(data, dict):
        return []
    out = []
    for key, value in data.items():
        if isinstance(value, dict) and value.get("available") is False:
            out.append(f"{key} 不可用：{value.get('reason') or value.get('label') or ''}"[:160])
        elif key in ("caveats", "warnings") and isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    out.append(f"{item.get('code', key)}：{item.get('label', '')}")
                else:
                    out.append(str(item)[:160])
    return out[:12]


def call_capability(capability_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """按 capability_id（或直接给 `/api/...` 路径）取一条登记的数。"""
    if capability_id.startswith("/api/"):
        path = capability_id
    else:
        cap = CURATED_BY_ID.get(capability_id)
        if cap is None:
            raise McpDataError(
                f"未登记的 capability_id：{capability_id}。先调 list_capabilities 看有哪些")
        path = cap.path
    return fetch(path, params)


def describe_capability(capability_id: str) -> dict[str, Any]:
    cap = CURATED_BY_ID.get(capability_id)
    if cap is None:
        raise McpDataError(f"未登记的 capability_id：{capability_id}")
    return capability_entry(cap)
