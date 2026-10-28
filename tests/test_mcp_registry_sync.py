"""MCP 服务的「写着有其实没接」自查。

全部是静态读源码／纯函数校验，不连库、不打网络：这一族测试坏了必须能立刻发现，
而不是等到智能体调用时才暴露。台账文件是 docs/MCP服务登记表.md。
"""
import re
from pathlib import Path

import pytest

from mcp_service import capabilities as caps
from mcp_service import client

ROOT = Path(__file__).resolve().parents[1]
SERVER_PY = (ROOT / "mcp_service" / "server.py").read_text(encoding="utf-8")
SYSTEM_TSX = (ROOT / "frontend" / "src" / "pages" / "System.tsx").read_text(encoding="utf-8")
PRESETS_TS = (ROOT / "frontend" / "src" / "lib" / "aiPresets.ts").read_text(encoding="utf-8")


def test_ledger_doc_matches_registry_row_by_row():
    """台账三张表与登记表逐条对齐：少一行、多一行、端点写岔都算红。"""
    diff = caps.doc_registry_diff()
    assert diff["ok"], diff


@pytest.mark.parametrize("cap_id", [c.id for c in caps.CURATED])
def test_curated_endpoint_really_exists_and_is_read_only(cap_id):
    """每条登记的来源端点必须真的以 `@app.get/head` 存在——不能对着一个不存在的接口写台账。"""
    cap = caps.CURATED_BY_ID[cap_id]
    entry = caps.endpoint_index().get(cap.path)
    assert entry is not None, f"{cap_id} 的端点 {cap.path} 不在只读索引里"
    assert not entry["excluded"]


def test_tool_names_are_registered_and_backed_by_a_function():
    """A/B 层每条都必须有一个同名工具函数，且真的挂上了 `@mcp.tool(name=...)`。"""
    missing = [t for t in caps.AGENT_TOOLS
               if f'name="{t}"' not in SERVER_PY or f"def {t}(" not in SERVER_PY]
    assert not missing, f"登记了但没接到调用点的工具：{missing}"
    assert SERVER_PY.count('@mcp.tool(name=') == len(caps.AGENT_TOOLS)


def test_generic_tools_exist():
    for name in ("list_capabilities", "capability_data", "data_freshness"):
        assert f"def {name}(" in SERVER_PY


@pytest.mark.parametrize("cap_id", [c.id for c in caps.CURATED if c.layer in (caps.LAYER_A, caps.LAYER_B)])
def test_agent_tool_path_is_also_on_the_screen_checklist(cap_id):
    """给智能体的每一格数，屏幕自检清单里也得有同一条 —— 否则两份迟早分家。"""
    cap = caps.CURATED_BY_ID[cap_id]
    screen_path = cap.path.removeprefix("/api")
    assert screen_path in SYSTEM_TSX, f"{cap.path} 不在 System.tsx 的能力清单里"


def test_refresh_parameter_is_never_exposed():
    """重算类参数一处都不许从 MCP 侧露出去（会抢 8910 的互斥、污染正在跑的窗口）。"""
    for cap in caps.CURATED:
        names = {p["name"] for p in caps.capability_entry(cap)["params"]}
        assert "refresh" not in names, f"{cap.id} 把 refresh 露出来了"


def test_ai_preset_sources_are_all_in_the_read_only_index():
    """页面问答用的那批源（前端另一份台账）必须也在同一个索引里，不能各自漂。"""
    for raw in re.findall(r"'(/[^']+)'", PRESETS_TS):
        path = raw.split("?")[0].rstrip("=")
        if not path.startswith("/"):
            continue
        full = f"/api{path}"
        entry = caps.endpoint_index().get(full)
        assert entry is not None, f"预设源 {raw} 对应的 {full} 不在只读索引里"


def test_unknown_parameter_is_rejected_rather_than_ignored():
    """FastAPI 会静默丢掉不认识的查询键 —— 「筛了个寂寞」就是这么来的，必须在客户端就拒。"""
    with pytest.raises(client.McpDataError):
        client._coerce("/api/quant/sector-heat", {"limt": 5})


def test_non_read_only_path_is_rejected():
    with pytest.raises(client.McpDataError):
        client._coerce("/api/review/capture", {})


@pytest.mark.parametrize("path,why", sorted(caps.EXCLUDE_INDEX.items()))
def test_excluded_probe_endpoints_stay_unusable(path, why):
    assert path in caps.endpoint_index()
    assert caps.endpoint_index()[path]["excluded"] == why
    with pytest.raises(client.McpDataError):
        client._coerce(path, {})


def test_out_of_range_parameter_is_rejected():
    with pytest.raises(client.McpDataError):
        client._coerce("/api/quant/quadrant", {"limit": 99999})


def test_mcp_board_routes_are_wired():
    """三条界面侧端点必须真的存在（并入后由 server.py 提供服务）。"""
    index = caps.endpoint_index()
    for path in ("/api/mcp/registry", "/api/mcp/status"):
        assert path in index, f"{path} 没接上"
    assert (ROOT / "vr" / "mcpboard.py").exists()
    vr_app = (ROOT / "vr" / "app.py").read_text(encoding="utf-8")
    assert '@app.post("/api/mcp/try")' in vr_app


def test_registry_payload_shape():
    payload = caps.registry_payload()
    assert payload["counts"]["A"] == len(caps.capabilities_of_layer(caps.LAYER_A))
    assert payload["counts"]["tools"] == len(caps.AGENT_TOOLS)
    assert payload["service_name"] == caps.SERVICE_NAME
    assert all(c["handler"] for c in payload["capabilities"])


def test_layer_ids_unique():
    ids = [c.id for c in caps.CURATED]
    assert len(ids) == len(set(ids))
    paths = [c.path for c in caps.CURATED]
    assert len(paths) == len(set(paths)), "同一个端点登记了两次"
