"""Loopback-only Responses to Chat Completions translation for compatible sources.

The pinned engine speaks only the Responses wire API, so a gateway that serves
/chat/completions cannot be reached directly. This module terminates the engine's
Responses call on 127.0.0.1 and re-issues it as a streaming chat completion. The
provider credential stays in this process; the engine gets a one-shot loopback
token instead.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from .evidence import EvidenceError

MAX_BODY = 8_000_000
SESSION_TTL = 2 * 3600
MAX_SESSIONS = 8
UPSTREAM_TIMEOUT = httpx.Timeout(connect=15, read=180, write=30, pool=15)
SEP = "__"
STATE_KEY = "responses_bridge"


def _note(ok: bool, reason: str) -> None:
    """上游坏在哪，得留下一个有人读的记录（判据 06 §1：`manage.py doctor` 逐项读
    `jobs/state.json` 的每个键；`reason` 是唯一的归因键名）。

    这把闸门只在**整条上游调用**的粒度上出声，不在每个 SSE 分片上 —— 每片都记会把
    那份共享状态文件刷成噪音。异常原文也不整段外传：连接类错误的字符串里带上游地址，
    只留类型名与截断后的原因。
    """
    try:
        from duanxian import jobs
        jobs.record(STATE_KEY, ok=ok, detail={"reason": reason[:200]})
    except Exception:  # noqa: BLE001  记账通道坏了不能反过来影响翻译本身
        pass


def _note_failure(exc: BaseException | None, where: str) -> None:
    detail = f"{type(exc).__name__}：{str(exc)[:120]}" if exc is not None else ""
    _note(False, where + (f"｜{detail}" if detail else ""))


def _note_success() -> None:
    _note(True, "Responses 转换层上游正常返回")


def status_message(code: int) -> str:
    return {
        400: "兼容端点拒绝了请求格式，请确认模型名称并检查该渠道是否支持工具调用",
        401: "兼容端点拒绝了密钥，请检查 API 密钥",
        403: "兼容端点拒绝了访问权限，请检查账户或渠道权限",
        404: "兼容端点没有该模型，请填写账户可用的模型标识",
        413: "本轮上下文超过了兼容端点的请求上限",
        422: "兼容端点拒绝了请求参数，请检查模型是否支持工具调用",
        429: "兼容端点限流或额度不足，请稍后重试",
        500: "兼容端点返回了服务端错误，请稍后重试",
        502: "兼容端点的上游渠道不可用",
        503: "兼容端点暂时不可用",
    }.get(code, "兼容端点请求失败，请检查地址、模型与额度")


class Sessions:
    def __init__(self):
        self.lock = threading.Lock()
        self.items: dict[str, dict] = {}

    def add(self, base_url: str, key: str) -> str:
        with self.lock:
            now = time.monotonic()
            for stale in [token for token, value in self.items.items() if value["until"] < now]:
                self.items.pop(stale, None)
            if len(self.items) >= MAX_SESSIONS:
                raise EvidenceError("本机模型转换层的测试通道已占满，请稍后重试")
            token = secrets.token_urlsafe(32)
            self.items[token] = {"base_url": base_url, "key": key, "until": now + SESSION_TTL}
            return token

    def get(self, token: str) -> dict | None:
        with self.lock:
            value = self.items.get(token)
            return dict(value) if value and value["until"] >= time.monotonic() else None

    def drop(self, token: str) -> None:
        with self.lock:
            self.items.pop(token, None)


_sessions = Sessions()
_server: ThreadingHTTPServer | None = None
_server_lock = threading.Lock()


def _text(parts) -> str:
    if isinstance(parts, str):
        return parts
    return "\n".join(str(part.get("text", "")) for part in parts or [] if isinstance(part, dict)
                     and part.get("type") in ("input_text", "output_text", "text"))


def flatten(namespace: str, name: str) -> str:
    return f"{namespace}{SEP}{name}" if namespace else name


def unflatten(call_name: str, reverse: dict) -> tuple[str, str]:
    namespace, bare = reverse.get(call_name, ("", call_name))
    if not namespace and SEP in call_name:
        head, tail = call_name.split(SEP, 1)
        if head.startswith("mcp"):
            namespace, bare = head, tail
    return namespace, bare


def tool_maps(responses_tools: list) -> tuple[list, dict]:
    """Flatten namespaced tools into chat function names, keeping the way back."""
    flat, reverse = [], {}
    for tool in responses_tools or []:
        if not isinstance(tool, dict) or tool.get("type") not in ("namespace", "function"):
            continue
        namespace = str(tool.get("name", "")) if tool.get("type") == "namespace" else ""
        for item in tool.get("tools") or ([tool] if not namespace else []):
            name = str(item.get("name", ""))
            if not name or item.get("type") not in (None, "function"):
                continue
            call = flatten(namespace, name)
            if len(call) > 64:
                continue
            if namespace:
                reverse[call] = (namespace, name)
            flat.append({"type": "function", "function": {
                "name": call, "description": str(item.get("description", "")),
                "parameters": item.get("parameters") or {"type": "object", "properties": {}},
            }})
    return flat, reverse


def to_chat(request: dict) -> dict:
    """Translate one engine Responses call into a chat completion request."""
    messages = []
    instructions = request.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    for item in request.get("input") or []:
        kind = item.get("type")
        if kind == "message":
            content = item.get("content")
            if isinstance(content, list) and any(part.get("type") not in ("input_text", "output_text")
                                                 for part in content if isinstance(part, dict)):
                raise EvidenceError("本接入方式只支持文本对话，图片或文件类输入请改用 Responses 来源")
            role = {"developer": "system", "user": "user", "assistant": "assistant"}.get(item.get("role"))
            text = _text(content)
            if role and text:
                messages.append({"role": role, "content": text})
        elif kind == "function_call":
            messages.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": str(item.get("call_id", "")), "type": "function",
                "function": {"name": flatten(str(item.get("namespace", "")), str(item.get("name", ""))),
                             "arguments": str(item.get("arguments") or "{}")},
            }]})
        elif kind == "function_call_output":
            messages.append({"role": "tool", "tool_call_id": str(item.get("call_id", "")),
                             "content": _text(item.get("output")) or "{}"})
    tools, _ = tool_maps(request.get("tools"))
    payload = {"model": str(request.get("model", "")), "messages": messages, "stream": True,
               "stream_options": {"include_usage": True}, "tool_choice": "auto",
               "parallel_tool_calls": bool(request.get("parallel_tool_calls", True))}
    if tools:
        payload["tools"] = tools
    if not payload["model"] or not messages:
        raise EvidenceError("引擎请求缺少模型或对话内容，本机转换层未转发")
    return payload


class Stream:
    """Rebuild a Responses item sequence from chat completion deltas.

    Tool calls are buffered until the upstream finishes: the engine only consumes
    a function call once its arguments parse, so a late complete item is enough.
    """

    def __init__(self, response_id: str, model: str):
        self.response_id, self.model = response_id, model
        self.output: list[dict] = []
        self.text = ""
        self.calls: dict[int, dict] = {}
        self.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def _response(self, status: str) -> dict:
        return {"id": self.response_id, "object": "response", "created_at": int(time.time()),
                "status": status, "model": self.model, "output": self.output, "usage": self.usage}

    def head(self) -> list[bytes]:
        return [_sse("response.created", {"type": "response.created", "response": self._response("in_progress")})]

    def text_delta(self, delta: str) -> list[bytes]:
        out = []
        if not self.text:
            item = {"id": f"msg_{self.response_id}", "type": "message", "role": "assistant",
                    "status": "in_progress", "content": []}
            self.output.append(item)
            out.append(_sse("response.output_item.added", {"type": "response.output_item.added",
                                                           "output_index": len(self.output) - 1, "item": item}))
        self.text += delta
        out.append(_sse("response.output_text.delta", {"type": "response.output_text.delta",
                                                       "item_id": self.output[-1]["id"],
                                                       "output_index": len(self.output) - 1,
                                                       "content_index": 0, "delta": delta}))
        return out

    def collect_call(self, piece: dict) -> None:
        for call in piece:
            index = int(call.get("index") or 0)
            slot = self.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if call.get("id"):
                slot["id"] = str(call["id"])
            function = call.get("function") or {}
            if function.get("name"):
                slot["name"] = str(function["name"])
            if isinstance(function.get("arguments"), str):
                slot["arguments"] += function["arguments"]

    def tail(self, reverse: dict) -> list[bytes]:
        out = []
        if self.text:
            index = 0
            out.append(_sse("response.output_text.done", {"type": "response.output_text.done",
                                                          "item_id": self.output[0]["id"], "output_index": index,
                                                          "content_index": 0, "text": self.text}))
            self.output[0]["content"] = [{"type": "output_text", "text": self.text, "annotations": []}]
            self.output[0]["status"] = "completed"
            out.append(_sse("response.output_item.done", {"type": "response.output_item.done",
                                                          "output_index": index, "item": self.output[0]}))
        for slot in self.calls.values():
            namespace, name = unflatten(slot["name"], reverse)
            item = {"id": f"fc_{self.response_id}_{len(self.output)}", "type": "function_call",
                    "status": "completed", "name": name, "call_id": slot["id"] or f"call_{secrets.token_hex(4)}",
                    "arguments": slot["arguments"] or "{}"}
            if namespace:
                item["namespace"] = namespace
            index = len(self.output)
            self.output.append(item)
            out.append(_sse("response.output_item.added", {"type": "response.output_item.added", "output_index": index,
                                                           "item": {key: value for key, value in item.items()
                                                                    if key != "arguments"}}))
            out.append(_sse("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta",
                                                                       "item_id": item["id"], "output_index": index,
                                                                       "delta": item["arguments"]}))
            out.append(_sse("response.function_call_arguments.done", {"type": "response.function_call_arguments.done",
                                                                      "item_id": item["id"], "output_index": index,
                                                                      "arguments": item["arguments"]}))
            out.append(_sse("response.output_item.done", {"type": "response.output_item.done",
                                                          "output_index": index, "item": item}))
        out.append(_sse("response.completed", {"type": "response.completed", "response": self._response("completed")}))
        return out


def _sse(type_name: str, payload: dict) -> bytes:
    return f"event: {type_name}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "astock-responses-bridge"

    def log_message(self, *args):
        return  # Prompts and provider traffic never reach the service log.

    def _reject(self, status: int, message: str):
        payload = json.dumps({"error": {"message": message, "type": "invalid_request_error"}},
                             ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _chunk(self, data: bytes):
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()

    def do_POST(self):
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            length = -1
        if length > MAX_BODY:
            # Undrainable body: end the connection instead of desyncing keep-alive.
            self.close_connection = True
            self._reject(413, "本轮上下文过大，兼容端点无法转发")
            return
        # Always consume the body first, or a rejected keep-alive request would be
        # parsed as the next request line.
        raw = self.rfile.read(length) if length > 0 else b""
        if self.path.split("?")[0] not in ("/v1/responses", "/responses"):
            self._reject(404, "本机模型转换层只提供 /v1/responses")
            return
        header = self.headers.get("authorization", "")
        session = _sessions.get(header[7:].strip()) if header.startswith("Bearer ") else None
        if session is None:
            self._reject(401, "本机模型转换层的会话已失效，请重新测试连接")
            return
        if not raw:
            self._reject(400, "请求为空")
            return
        try:
            request = json.loads(raw)
            chat = to_chat(request)
            reverse = tool_maps(request.get("tools"))[1]
        except EvidenceError as exc:
            self._reject(400, str(exc))
            return
        except (ValueError, UnicodeDecodeError):
            self._reject(400, "请求体不是有效的 JSON")
            return
        stream = Stream("resp_" + secrets.token_hex(8), chat["model"])
        try:
            with httpx.Client(timeout=UPSTREAM_TIMEOUT) as client:
                with client.stream("POST", session["base_url"] + "/chat/completions",
                                   headers={"authorization": "Bearer " + session["key"],
                                            "content-type": "application/json",
                                            "accept": "text/event-stream"}, json=chat) as upstream:
                    if upstream.status_code != 200:
                        upstream.read()
                        _note_failure(None, f"上游返回 HTTP {upstream.status_code}")
                        self._reject(upstream.status_code, status_message(upstream.status_code))
                        return
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("cache-control", "no-cache")
                    self.send_header("transfer-encoding", "chunked")
                    self.end_headers()
                    for data in stream.head():
                        self._chunk(data)
                    for line in upstream.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        body = line[5:].strip()
                        if not body or body == "[DONE]":
                            continue
                        try:
                            piece = json.loads(body)
                        except ValueError:
                            continue
                        usage = piece.get("usage")
                        if isinstance(usage, dict):
                            stream.usage = {"input_tokens": int(usage.get("prompt_tokens") or 0),
                                            "output_tokens": int(usage.get("completion_tokens") or 0),
                                            "total_tokens": int(usage.get("total_tokens") or 0)}
                        for choice in piece.get("choices") or []:
                            delta = choice.get("delta") or {}
                            content = delta.get("content")
                            if isinstance(content, str) and content:
                                for data in stream.text_delta(content):
                                    self._chunk(data)
                            if delta.get("tool_calls"):
                                stream.collect_call(delta["tool_calls"])
                    for data in stream.tail(reverse):
                        self._chunk(data)
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                    _note_success()
        except (httpx.HTTPError, OSError) as exc:
            # 引擎只会说"流被截断"，真因（连接被拒、读超时、对端断）在这里，也only在这里。
            # 不说话就等于把一次付费调用的失败原因丢给了一个看不见的地方。
            _note_failure(exc, "Responses 转换层上游流中断")
            return  # The engine classifies the broken stream itself.


def _start() -> str:
    global _server
    with _server_lock:
        if _server is None:
            _server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            _server.daemon_threads = True
            threading.Thread(target=_server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{_server.server_address[1]}/v1"


def open_session(base_url: str, key: str) -> tuple[str, str]:
    """Return the loopback Responses address plus the one-shot engine credential."""
    return _start(), _sessions.add(base_url.rstrip("/"), key)


def close_session(token: str) -> None:
    _sessions.drop(token)
