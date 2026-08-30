"""MCP integration — our own async client on the official `mcp` SDK.

Public API: config loading/mutation, the connection manager, and tool wrapping.
"""

from __future__ import annotations

from integrations.mcp.client import MCPManager
from integrations.mcp.config import (
    MCPServerDef,
    delete_global_server,
    load_mcp_servers,
    patch_global_server,
    put_global_server,
    read_global,
)
from integrations.mcp.tools import build_callables, tool_name

__all__ = [
    "MCPManager",
    "MCPServerDef",
    "build_callables",
    "delete_global_server",
    "load_mcp_servers",
    "patch_global_server",
    "put_global_server",
    "read_global",
    "tool_name",
]
