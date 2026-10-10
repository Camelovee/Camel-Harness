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
        self._tools: list[Tool] = []
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
                    raw = json.loads(await asyncio.to_thread(self._config_path.read_text, "utf-8"))
                    if not isinstance(raw, dict):
                        raise ValueError("MCP 配置顶层必须是对象")
                    servers = raw.get("mcpServers", {})
                    if not isinstance(servers, dict):
                        raise ValueError("MCP 配置中的 mcpServers 必须是对象")
                    for name, value in servers.items():
                        try:
                            await self._connect(str(name), value, stack)
                        except Exception as exc:
                            self.errors.append(f"{name}: {exc}")
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
            self._tools.clear()
            self._ready.set()

    async def discover(self) -> tuple[Tool, ...]:
        if self._failure is not None:
            raise self._failure
        return tuple(self._tools)

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

    async def _connect(self, name: str, value: object, stack: AsyncExitStack) -> None:
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
        # 失败连接就地清理，成功连接按进入顺序交给同一任务的外层栈。
        async with AsyncExitStack() as connection:
            reader, writer = await connection.enter_async_context(stdio_client(parameters))
            session = await connection.enter_async_context(ClientSession(reader, writer))
            await asyncio.wait_for(session.initialize(), 30)
            catalog = await asyncio.wait_for(session.list_tools(), 30)
            tools = [
                McpRemoteTool(
                    name,
                    item.name,
                    item.description or "MCP 外部工具",
                    dict(item.inputSchema),
                    session,
                )
                for item in catalog.tools
            ]
            stack.push_async_callback(connection.pop_all().aclose)
            self._tools.extend(tools)
