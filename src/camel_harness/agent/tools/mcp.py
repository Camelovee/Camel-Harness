from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .registry import ToolProvider
from .types import Tool, ToolCall, ToolContext, ToolResult, ToolRisk

logger = logging.getLogger(__name__)


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
        # 远端调用没有独立超时，靠 executor 的 60s；超时只是取消协程，结果未知。
        self.timeout_seconds = 60

    async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
        # Server 的 cwd 已在进程级固定为工作区，单次调用不再使用 tool_context。
        del tool_context
        mcp_result = await self._session.call_tool(self._remote_name, tool_call.arguments)
        content_parts: list[str] = []
        for block in mcp_result.content:
            text = getattr(block, "text", None)
            if text is not None:
                content_parts.append(str(text))
            else:
                content_data = block.model_dump(mode="json")
                content_parts.append(json.dumps(content_data, ensure_ascii=False))
        return ToolResult(
            tool_call.call_id,
            tool_call.name,
            "\n".join(content_parts),
            is_error=bool(mcp_result.isError),
        )


class McpManager(ToolProvider):
    """管理多个 stdio MCP Server 的连接、发现与关闭。"""

    def __init__(self, workspace: Path, config_path: Path) -> None:
        self._workspace = workspace
        self._config_path = config_path
        self._remote_tools: list[Tool] = []
        self.errors: list[str] = []
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._closed = False
        self._entered = False
        self._failure: BaseException | None = None

    async def start(self) -> None:
        """连接上下文属于唯一任务，调用方仅等待初始化结果。"""
        if self._closed:
            raise RuntimeError("MCP 管理器已关闭")
        if self._task is None:
            self._task = asyncio.create_task(self._serve())
        try:
            await self._ready.wait()
        except asyncio.CancelledError:
            await self.close()
            raise
        if self._failure is not None:
            raise self._failure
        if self._closed:
            raise RuntimeError("MCP 初始化已取消")

    async def _serve(self) -> None:
        """所有 SDK 上下文在此任务内进入和退出，不跨任务转移取消作用域。"""
        try:
            self._entered = True
            if self._closed:
                return
            async with AsyncExitStack() as stack:
                if self._config_path.is_file():
                    mcp_config = json.loads(
                        await asyncio.to_thread(self._config_path.read_text, "utf-8")
                    )
                    if not isinstance(mcp_config, dict):
                        raise ValueError("MCP 配置顶层必须是对象")
                    server_configs = mcp_config.get("mcpServers", {})
                    if not isinstance(server_configs, dict):
                        raise ValueError("MCP 配置中的 mcpServers 必须是对象")
                    for server_name, server_config in server_configs.items():
                        try:
                            await self._connect(str(server_name), server_config, stack)
                        except Exception as exc:
                            # 单个 Server 失败只记录错误，其余工具仍然可用。
                            self.errors.append(f"{server_name}: {exc}")
                self._ready.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            if not self._closed:
                self._failure = RuntimeError("MCP 连接任务意外取消")
                logger.error("MCP 连接任务意外取消")
        except BaseException as exc:
            self._failure = exc
            logger.error("MCP 连接任务失败：%s", type(exc).__name__)
        finally:
            self._remote_tools.clear()
            self._ready.set()

    async def discover(self) -> tuple[Tool, ...]:
        if self._failure is not None:
            raise self._failure
        return tuple(self._remote_tools)

    async def close(self) -> None:
        """取消等待者不取消资源清理；初始化阶段可取消连接任务以打断握手。"""
        stopping = self._closed
        self._closed = True
        self._stop.set()
        if self._task is not None:
            if not stopping and self._entered and not self._ready.is_set():
                self._task.cancel()
            await asyncio.shield(self._task)
        if self._failure is not None:
            raise self._failure

    async def _connect(
        self, server_name: str, server_config: object, stack: AsyncExitStack
    ) -> None:
        if not isinstance(server_config, dict):
            raise ValueError("Server 配置必须是对象")
        command = server_config.get("command")
        if not isinstance(command, str) or not command:
            raise ValueError("缺少 command")
        server_arguments = server_config.get("args", [])
        server_environment = server_config.get("env")
        if not isinstance(server_arguments, list) or not all(
            isinstance(item, str) for item in server_arguments
        ):
            raise ValueError("args 必须是字符串数组")
        if server_environment is not None and (
            not isinstance(server_environment, dict)
            or not all(
                isinstance(key, str) and isinstance(item, str)
                for key, item in server_environment.items()
            )
        ):
            raise ValueError("env 必须是字符串映射")
        parameters = StdioServerParameters(
            command=command,
            args=server_arguments,
            env=server_environment,
            # Server 必须继承工作区而不是 Harness 进程的当前目录。
            cwd=self._workspace,
        )
        # 失败连接就地清理，成功连接按进入顺序交给同一任务的外层栈。
        async with AsyncExitStack() as connection:
            reader, writer = await connection.enter_async_context(stdio_client(parameters))
            session = await connection.enter_async_context(ClientSession(reader, writer))
            # 握手与工具发现各 30s，避免启动阶段无限挂起。
            await asyncio.wait_for(session.initialize(), 30)
            catalog = await asyncio.wait_for(session.list_tools(), 30)
            remote_tools = [
                McpRemoteTool(
                    server_name,
                    item.name,
                    item.description or "MCP 外部工具",
                    dict(item.inputSchema),
                    session,
                )
                for item in catalog.tools
            ]
            stack.push_async_callback(connection.pop_all().aclose)
            self._remote_tools.extend(remote_tools)
