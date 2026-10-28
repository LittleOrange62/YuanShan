"""YuanShan 展示层数据的只读 MCP 服务包。

台账：docs/MCP服务登记表.md（人类读）＋ mcp_service/capabilities.py（机器读），两者逐条对齐。
"""
from .capabilities import CURATED, SERVICE_NAME, registry_payload
from .client import McpDataError, base_url, fetch

__all__ = ["CURATED", "SERVICE_NAME", "registry_payload", "McpDataError", "base_url", "fetch"]
