from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from camel_backend.web_settings import WebSettingsStore
from camel_harness.agent import Agent, AgentBusyError, AgentNotRunningError
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.session import SessionManager
from camel_harness.agent.state import SteerResult

AgentBuilder = Callable[[str, Path, AgentSettings], Awaitable[Agent]]


class RunService:
    """按 Session 惰性定位唯一 Agent，并持有全部运行期生命周期规则。"""

    def __init__(
        self,
        build_agent: AgentBuilder,
        sessions: SessionManager,
        settings: WebSettingsStore,
    ) -> None:
        self._build_agent = build_agent
        self._sessions = sessions
        self._settings = settings
        self._agents: dict[str, Agent] = {}
        self._agent_settings: dict[str, AgentSettings] = {}
        self._lock = asyncio.Lock()

    async def require_agent(self, session_id: str, model_id: str | None = None) -> Agent:
        """返回绑定指定 Session 的唯一 Agent，必要时惰性创建。"""
        async with self._lock:
            return await self._require_agent_locked(session_id, model_id)

    @asynccontextmanager
    async def acquire(self, session_id: str, model_id: str | None = None) -> AsyncIterator[Agent]:
        """返回可启动 Run 的 Agent；互斥保持到首事件交付，防止并发请求替换待启动实例。"""
        async with self._lock:
            agent = await self._require_agent_locked(session_id, model_id)
            if agent.is_running:
                raise AgentBusyError("会话已有正在运行的 Agent")
            if model_id:
                await self._sessions.update(session_id, model_id=model_id)
            yield agent

    async def steer(self, session_id: str, content: str) -> SteerResult:
        """向活动 Run 追加输入；不存在活动 Run 时拒绝。"""
        agent = self._agents.get(session_id)
        if agent is None:
            raise AgentNotRunningError("会话没有活动 Run")
        return await agent.steer(content)

    async def abort(self, session_id: str) -> None:
        """请求中断活动 Run；不存在或已经结束时不视为成功。"""
        agent = self._agents.get(session_id)
        if agent is None or not await agent.abort():
            raise AgentNotRunningError("会话没有活动 Run")

    def is_running(self, session_id: str) -> bool:
        """返回该会话当前是否存在活动 Run。"""
        agent = self._agents.get(session_id)
        return agent is not None and agent.is_running

    async def release(self, session_id: str) -> bool:
        """移除空闲 Agent；活动 Run 存在时拒绝释放。"""
        async with self._lock:
            agent = self._agents.get(session_id)
            if agent is None:
                return True
            if agent.is_running:
                return False
            self._agents.pop(session_id)
            self._agent_settings.pop(session_id, None)
        await agent.close()
        return True

    async def close(self) -> None:
        """关闭所有按需创建的 Agent。"""
        async with self._lock:
            agents = tuple(self._agents.values())
            self._agents.clear()
            self._agent_settings.clear()
        await asyncio.gather(*(agent.close() for agent in agents))

    async def _require_agent_locked(self, session_id: str, model_id: str | None) -> Agent:
        metadata = await self._sessions.get(session_id)
        if metadata is None:
            raise KeyError(f"会话不存在：{session_id}")
        _, settings = await self._settings.resolve(model_id or metadata.model_id)
        current = self._agents.get(session_id)
        if current is not None and self._agent_settings[session_id] == settings:
            return current
        if current is not None and current.is_running:
            raise AgentBusyError("会话已有正在运行的 Agent，结束后才能切换模型")
        agent = await self._build_agent(session_id, Path(metadata.workspace), settings)
        self._agents[session_id] = agent
        self._agent_settings[session_id] = settings
        if current is not None:
            await current.close()
        return agent
