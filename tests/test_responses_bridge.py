"""Protocol contract tests for the loopback Responses -> Chat Completions bridge.

The request and tool shapes below were captured from the pinned Codex 0.153.4
engine; a flattened tool name without its namespace is silently turned into an
"unsupported call" by that engine, so the namespace round trip is the point.
"""
import json
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from review_agent import responses_bridge as bridge
from review_agent.evidence import EvidenceError
from review_agent.runtime import CHAT_PROVIDERS, config_for, connection

ENGINE_REQUEST = {
    "model": "gateway-model",
    "instructions": "你是公开市场复盘分析助手。",
    "input": [
        {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "规则"}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "两天涨停家数变化"}]},
        {"type": "function_call", "name": "list_evidence", "namespace": "mcp__astock",
         "arguments": "{}", "call_id": "call_1"},
        {"type": "function_call_output", "call_id": "call_1",
         "output": [{"type": "input_text", "text": "Wall time: 0.01 seconds\nOutput:"},
                    {"type": "input_text", "text": '{"anchor":"2026-09-23"}'}]},
    ],
    "tools": [
        {"type": "function", "name": "request_user_input", "description": "plan only",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "namespace", "name": "mcp__astock", "tools": [
            {"type": "function", "name": "list_evidence", "description": "list",
             "parameters": {"type": "object", "properties": {}}},
            {"type": "function", "name": "submit_answer", "description": "submit",
             "parameters": {"type": "object", "properties": {"status": {"type": "string"}}}},
        ]},
    ],
    "tool_choice": "auto", "parallel_tool_calls": True, "stream": True, "store": False,
    "include": ["reasoning.encrypted_content"], "reasoning": {"effort": "low"},
}


def chat_sse(events):
    return "".join(f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events).encode()


class Gateway:
    """Minimal stand-in for a chat-completions-only gateway."""

    def __init__(self, script, status=200, body="", gap=0.0):
        self.script, self.status, self.body, self.gap = script, status, body, gap
        self.received = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                outer.received.append({"path": self.path, "auth": self.headers.get("authorization"),
                                       "body": json.loads(self.rfile.read(int(self.headers["content-length"])))})
                if outer.status != 200:
                    payload = outer.body.encode()
                    self.send_response(outer.status)
                    self.send_header("content-length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()
                for chunk in outer.script[len(outer.received) - 1]:
                    self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                    # gap>0 模拟"一直有字节、迟迟不收尾"的渠道：读超时不会触发，
                    # 只有整条回答的总时长闸能把它停住。
                    if outer.gap:
                        time.sleep(outer.gap)
                self.wfile.write(b"0\r\n\r\n")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def delta(piece, finish=None):
    return chat_sse([{"id": "cmpl", "object": "chat.completion.chunk", "model": "gateway-model",
                      "choices": [{"index": 0, "delta": piece, "finish_reason": finish}]}])


def call_events():
    return [
        delta({"role": "assistant", "content": None}),
        delta({"tool_calls": [{"index": 0, "id": "call_2", "type": "function",
                               "function": {"name": "mcp__astock__submit_answer", "arguments": '{"stat'}}]}),
        delta({"tool_calls": [{"index": 0, "function": {"arguments": 'us":"incomplete"}'}}]}),
        delta({}, finish="tool_calls"),
        b"data: [DONE]\n\n",
    ]


def events_from(response):
    return [json.loads(line[5:].strip()) for line in response.iter_lines() if line.startswith("data:")]


def test_chat_sources_keep_their_identity_and_allow_only_local_http():
    for provider in CHAT_PROVIDERS:
        source, key = connection({"provider": provider, "model": "deepseek-chat",
                                  "baseURL": "http://192.168.176.68:3000/v1/", "apiKey": "gateway-key"})
        assert source == {"provider": provider, "model": "deepseek-chat", "baseURL": "http://192.168.176.68:3000/v1",
                          "timeout_seconds": 180}, "每条来源自带总时长闸，默认 180 秒"
        assert key == "gateway-key"
        assert config_for(None, source)["model_providers.astock_api"]["wire_api"] == "responses"
    for url in ['http://127.0.0.1:3000/v1', 'http://10.1.2.3:3000/v1', 'http://172.20.1.9/v1', 'http://[fd00::1234]:3000/v1']:
        assert connection({"provider": "new-api", "model": "m", "baseURL": url, "apiKey": "k"})[0]["baseURL"] == url
    for url in ['http://8.8.8.8:3000/v1', 'http://ollama.example.com:3000/v1', 'http://192.168.176.68:3000/v1?key=x']:
        with pytest.raises(EvidenceError, match="明文 http"):
            connection({"provider": "new-api", "model": "m", "baseURL": url, "apiKey": "k"})
    # Remote https stays valid, and plain http is still refused for Responses-only sources.
    assert connection({"provider": "openai-compat", "model": "m", "baseURL": "https://gateway.example/v1", "apiKey": "k"})[0]["provider"] == "openai-compat"
    with pytest.raises(EvidenceError):
        connection({"provider": "api-compatible", "model": "m", "baseURL": "http://192.168.176.68:3000/v1", "apiKey": "k"})


def test_engine_request_becomes_one_chat_call():
    chat = bridge.to_chat(ENGINE_REQUEST)
    assert chat["model"] == "gateway-model" and chat["stream"] is True
    assert [message["role"] for message in chat["messages"]] == ["system", "system", "user", "assistant", "tool"]
    assert chat["messages"][0]["content"] == "你是公开市场复盘分析助手。"
    assert chat["messages"][3]["tool_calls"][0]["function"]["name"] == "mcp__astock__list_evidence"
    assert chat["messages"][4]["content"] == 'Wall time: 0.01 seconds\nOutput:\n{"anchor":"2026-09-23"}'
    names = [tool["function"]["name"] for tool in chat["tools"]]
    assert names == ["request_user_input", "mcp__astock__list_evidence", "mcp__astock__submit_answer"]
    assert "reasoning" not in chat and "include" not in chat


def test_unsupported_content_types_stop_before_the_gateway():
    request = json.loads(json.dumps(ENGINE_REQUEST))
    request["input"].append({"type": "message", "role": "user",
                             "content": [{"type": "input_image", "image_url": "https://host/x.png"}]})
    with pytest.raises(EvidenceError, match="只支持文本"):
        bridge.to_chat(request)


def test_tool_call_arguments_reassemble_and_return_to_their_namespace(tmp_path):
    gateway = Gateway([call_events()])
    try:
        base_url, token = bridge.open_session(gateway.url, "gateway-key")
        with httpx.Client(timeout=20) as client:
            with client.stream("POST", base_url + "/responses",
                               headers={"authorization": "Bearer " + token}, json=ENGINE_REQUEST) as response:
                assert response.status_code == 200
                events = events_from(response)
        types = [event["type"] for event in events]
        assert types[0] == "response.created" and types[-1] == "response.completed"
        calls = [event["item"] for event in events if event["type"] == "response.output_item.done"
                 and event["item"]["type"] == "function_call"]
        assert calls == [{"id": calls[0]["id"], "type": "function_call", "status": "completed",
                          "name": "submit_answer", "call_id": "call_2",
                          "arguments": '{"status":"incomplete"}', "namespace": "mcp__astock"}]
        deltas = "".join(event["delta"] for event in events if event["type"] == "response.function_call_arguments.delta")
        assert deltas == '{"status":"incomplete"}'
        assert json.loads(events[-1]["response"]["output"][0]["arguments"]) == {"status": "incomplete"}
        assert gateway.received[0]["auth"] == "Bearer gateway-key"
    finally:
        gateway.close()
        bridge.close_session(token)


def test_bridge_rejects_unknown_tokens_and_other_paths():
    base_url, token = bridge.open_session("https://gateway.example/v1", "gateway-key")
    try:
        with httpx.Client(timeout=10) as client:
            for headers in ({"authorization": "Bearer " + "x" * 40}, {"authorization": "Bearer gateway-key"}):
                assert client.post(base_url + "/responses", headers=headers, json=ENGINE_REQUEST).status_code == 401
            assert client.post(base_url + "/chat/completions", headers={"authorization": "Bearer " + token},
                               json=ENGINE_REQUEST).status_code == 404
    finally:
        bridge.close_session(token)
    with httpx.Client(timeout=10) as client:
        assert client.post(base_url + "/responses", headers={"authorization": "Bearer " + token},
                           json=ENGINE_REQUEST).status_code == 401


def test_gateway_failure_is_classified_without_echoing_the_body(tmp_path):
    gateway = Gateway([], status=401, body='{"error":{"message":"bad key sk-PRIVATE_CANARY"}}')
    try:
        base_url, token = bridge.open_session(gateway.url, "sk-PRIVATE_CANARY")
        with httpx.Client(timeout=20) as client:
            response = client.post(base_url + "/responses", headers={"authorization": "Bearer " + token},
                                   json=ENGINE_REQUEST)
        assert response.status_code == 401
        assert "密钥" in response.json()["error"]["message"]
        assert "PRIVATE_CANARY" not in response.text
    finally:
        gateway.close()
        bridge.close_session(token)


def _turn_tool(name, arguments, call_id):
    return [delta({"role": "assistant", "content": None}),
            delta({"tool_calls": [{"index": 0, "id": call_id, "type": "function",
                                   "function": {"name": f"mcp__astock__{name}", "arguments": ""}}]}),
            delta({"tool_calls": [{"index": 0, "function": {"arguments": arguments}}]}),
            delta({}, finish="tool_calls"), b"data: [DONE]\n\n"]


def _turn_text(text):
    return [delta({"content": text[:2]}), delta({"content": text[2:]}),
            delta({}, finish="stop"), b"data: [DONE]\n\n"]


@pytest.mark.skipif(not shutil.which("node"), reason="the pinned engine needs Node")
def test_real_engine_completes_a_bounded_run_through_the_bridge(tmp_path, monkeypatch):
    from review_agent import runtime as runtime_module
    from review_agent.evidence import canonical, digest
    from review_agent.runtime import CLI, Runtime

    if not CLI.is_file():
        pytest.skip("pinned engine not installed; run npm ci --prefix runtime")
    gateway = Gateway([
        _turn_tool("list_evidence", "{}", "call_a"),
        _turn_tool("submit_answer", json.dumps({"status": "incomplete", "findings": [{"text": "探针未读取资料", "citations": []}],
                                                "gaps": ["连接测试没有市场资料"]}, ensure_ascii=False), "call_b"),
        _turn_text("分析完成"),
    ])
    seen = []
    real_environment = runtime_module.engine_environment

    def spy(home, key=""):
        seen.append(key)
        return real_environment(home, key)

    monkeypatch.setattr(runtime_module, "engine_environment", spy)
    try:
        source, key = connection({"provider": "new-api", "model": "gateway-model",
                                  "baseURL": gateway.url, "apiKey": "sk-REAL-GATEWAY-KEY"})
        state = tmp_path / "state"
        bundle = {"anchor": "2000-01-01", "dates": [], "evidence": [], "gaps": ["探针没有市场资料"],
                  "scope": "仅测试连接与工具调用"}
        bundle["revision"] = digest(bundle)
        turn_id = "bf" + "0" * 30
        result = Runtime(state, timeout=120).run({"source": source, "bundle": bundle, "turns": []},
                                                 "调用 list_evidence 一次，然后 submit_answer 提交 incomplete。",
                                                 key, turn_id, threading.Event(), lambda message: None)
        assert result["status"] == "incomplete"
        assert [finding["text"] for finding in result["findings"]] == ["探针未读取资料"]
        ledger = (state / "runs" / turn_id / "tools.jsonl").read_text(encoding="utf-8")
        assert '"tool":"list_evidence"' in ledger and '"tool":"submit_answer"' in ledger
        assert gateway.received[-1]["auth"] == "Bearer sk-REAL-GATEWAY-KEY"
        # The engine child process only ever receives the one-shot loopback token.
        assert seen and all(token and token != "sk-REAL-GATEWAY-KEY" for token in seen)
        assert "sk-REAL-GATEWAY-KEY" not in canonical({"source": source, "result": result})
    finally:
        gateway.close()


LOCAL_GATEWAY = "http://192.168.176.68:3000/v1"


def test_每条来源的总时长闸由请求带到转换层():
    """默认 180 秒、每条可填 30~600；界面上填错要当场说错，不许静默变成一个数。"""
    assert connection({"provider": "new-api", "model": "m", "baseURL": LOCAL_GATEWAY, "apiKey": "k"})[0][
        "timeout_seconds"] == bridge.UPSTREAM_TOTAL_SECONDS == 180
    assert connection({"provider": "new-api", "model": "m", "baseURL": LOCAL_GATEWAY, "apiKey": "k",
                       "timeoutSeconds": 45})[0]["timeout_seconds"] == 45
    assert connection({"provider": "new-api", "model": "m", "baseURL": LOCAL_GATEWAY, "apiKey": "k",
                       "timeoutSeconds": "90"})[0]["timeout_seconds"] == 90, "输入框传来字符串也要认"
    for blank in (None, ""):
        assert connection({"provider": "new-api", "model": "m", "baseURL": LOCAL_GATEWAY, "apiKey": "k",
                           "timeoutSeconds": blank})[0]["timeout_seconds"] == 180, "留空按默认，不报错"
    for bad in (0, 29, 601, "abc", []):
        with pytest.raises(EvidenceError, match="超时"):
            connection({"provider": "new-api", "model": "m", "baseURL": LOCAL_GATEWAY, "apiKey": "k",
                        "timeoutSeconds": bad})


def test_会话记住这一条来源的总闸():
    gateway = Gateway([[delta({"content": "好"}, finish="stop"), b"data: [DONE]\n\n"]])
    tokens = []
    try:
        tokens.append(bridge.open_session(gateway.url, "k", 45)[1])
        assert bridge._sessions.get(tokens[0])["total_seconds"] == 45
        tokens.append(bridge.open_session(gateway.url, "k")[1])
        assert bridge._sessions.get(tokens[1])["total_seconds"] == bridge.UPSTREAM_TOTAL_SECONDS
    finally:
        for token in tokens:
            bridge.close_session(token)
        gateway.close()


def test_上游一直吐字节却不收尾时总闸到点就停(monkeypatch):
    """读超时管不住这种渠道（每 0.3 秒挤一点，永远不 finish）。今天 8 token 短问 120 秒不答
    就是这一类：总闸不到点，"这次不通"就永远不会发生，自动切换也就永远等不到那次失败。

    断言方式是"这个测试能在 6 秒内跑完"：转换层若不写终止块、不关连接，引擎侧会对着
    keep-alive 一直读，httpx 就会抛读超时，这条测试当场红。
    """
    notes = []
    monkeypatch.setattr(bridge, "_note_failure", lambda exc, where: notes.append((exc, where)))
    monkeypatch.setattr(bridge, "_note_success", lambda: notes.append((None, "success")))
    dribble = [delta({"content": "第一段"}), delta({"content": "第二段"}), delta({"content": "第三段"})]
    gateway = Gateway([dribble], gap=.3)
    try:
        base_url, token = bridge.open_session(gateway.url, "k", total_seconds=.5)
        with httpx.Client(timeout=6) as client:
            with client.stream("POST", base_url + "/responses",
                               headers={"authorization": "Bearer " + token}, json=ENGINE_REQUEST) as response:
                lines = list(response.iter_lines())
        assert notes and notes[-1][1].startswith("Responses 转换层已发给引擎、上游未正常收尾"), notes
        assert "0.5 秒内没答完" in str(notes[-1][0]), notes[-1]
        assert any(line.startswith("data:") for line in lines), "半截内容照样要发给引擎，只是到点收尾"
    finally:
        gateway.close()
        bridge.close_session(token)


def test_三段计时的口径写死并且不是一刀切60秒():
    """用户拍的是"拆开三段"，不是 60 秒一刀：同一条链今天 39.7 秒成过、下午 120 秒不答。"""
    assert bridge.UPSTREAM_CONNECT_SECONDS == 10
    assert bridge.UPSTREAM_FIRST_BYTE_SECONDS == 75, "11-03 用户确认：这条网关最好一次成功 39.7 秒，45 秒余量太窄"
    assert bridge.UPSTREAM_TOTAL_SECONDS == 180
    assert bridge.UPSTREAM_TIMEOUT.connect == bridge.UPSTREAM_CONNECT_SECONDS
    assert bridge.UPSTREAM_TIMEOUT.read == bridge.UPSTREAM_FIRST_BYTE_SECONDS
