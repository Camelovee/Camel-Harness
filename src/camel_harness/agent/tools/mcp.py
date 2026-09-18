from __future__ import annotations

import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .registry import ToolProvider
from .types import Tool, ToolCall, ToolContext, ToolResult, ToolRisk


class McpRemoteTool(Tool):
    """把一个远端 MCP 工具映射为统一 Tool ABC。"""

    def __init__(
        self,
        server_name: str,
        remote_name: str,
        description: str,
        input_schema: dict[str, Any],
        session: ClientSession,
    ) -> None:
        self._remote_name = remote_name
        self._session = session
        self.namespace = f"mcp__{server_name}"
        self.name = remote_name
        self.description = description
        self.parameters = input_schema
        self.capabilities = frozenset({"external.mcp"})
        self.risk = ToolRisk.EXTERNAL
        self.timeout_seconds = 60

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        result = await self._session.call_tool(self._remote_name, call.arguments)
        parts: list[str] = []
        for block in result.content:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(str(text))
            else:
                dump = block.model_dump(mode="json")
                parts.append(json.dumps(dump, ensure_ascii=False))
        return ToolResult(
            call.call_id,
            call.name,
            "\n".join(parts),
            is_error=bool(result.isError),
        )


class McpManager(ToolProvider):
    """管理多个 stdio MCP Server 的连接、发现与关闭。"""

    def __init__(self, workspace: Path, config_path: Path) -> None:
        self._workspace = workspace
        self._config_path = config_path
        self._stack = AsyncExitStack()
        self._tools: list[Tool] = []
        self.errors: list[str] = []

    async def start(self) -> None:
        if not self._config_path.is_file():
            return
        raw: dict[str, Any] = json.loads(self._config_path.read_text("utf-8"))
        servers = raw.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("MCP 配置中的 mcpServers 必须是对象")
        for name, value in servers.items():
            try:
                await self._connect(str(name), value)
            except Exception as exc:
                # 单个插件故障不能阻止核心 Runtime 启动，错误保留给诊断入口。
                self.errors.append(f"{name}: {exc}")

    async def discover(self) -> tuple[Tool, ...]:
        return tuple(self._tools)

    async def close(self) -> None:
        await self._stack.aclose()

    async def _connect(self, name: str, value: object) -> None:
        if not isinstance(value, dict):
            raise ValueError("Server 配置必须是对象")
        command = value.get("command")
        if not isinstance(command, str) or not command:
            raise ValueError("缺少 command")
        raw_args = value.get("args", [])
        raw_env = value.get("env")
        if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
            raise ValueError("args 必须是字符串数组")
        if raw_env is not None and (
            not isinstance(raw_env, dict)
            or not all(
                isinstance(key, str) and isinstance(item, str) for key, item in raw_env.items()
            )
        ):
            raise ValueError("env 必须是字符串映射")
        parameters = StdioServerParameters(
            command=command,
            args=raw_args,
            env=raw_env,
            cwd=self._workspace,
        )
        reader, writer = await self._stack.enter_async_context(stdio_client(parameters))
        session = await self._stack.enter_async_context(ClientSession(reader, writer))
        await session.initialize()
        catalog = await session.list_tools()
        for item in catalog.tools:
            self._tools.append(
                McpRemoteTool(
                    name,
                    item.name,
                    item.description or "MCP 外部工具",
                    dict(item.inputSchema),
                    session,
                )
            )
