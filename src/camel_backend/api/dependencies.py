import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from camel_backend.web_settings import WebSettingsStore
from camel_harness.agent import Agent, AgentBusyError
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.session import SessionManager, SessionMetadata
from camel_harness.agent.tools import ApprovalCenter

WorkspacePicker = Callable[[], Awaitable[Path | None]]
AgentBuilder = Callable[[str, Path, AgentSettings], Awaitable[Agent]]


@dataclass(slots=True)
class HttpDependencies:
    """保存共享依赖，并按 Session 惰性定位唯一 Agent。"""

    workspace: Path
    _build_agent: AgentBuilder | None = field(default=None, init=False, repr=False)
    _agents: dict[str, Agent] = field(default_factory=dict, init=False, repr=False)
    _agent_settings: dict[str, AgentSettings] = field(default_factory=dict, init=False, repr=False)
    _agents_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _sessions: SessionManager | None = field(default=None, init=False, repr=False)
    _approvals: ApprovalCenter | None = field(default=None, init=False, repr=False)
    _select_workspace: WorkspacePicker | None = field(default=None, init=False, repr=False)
    _settings: WebSettingsStore | None = field(default=None, init=False, repr=False)

    def bind(
        self,
        build_agent: AgentBuilder,
        sessions: SessionManager,
        approvals: ApprovalCenter | None = None,
        select_workspace: WorkspacePicker | None = None,
        settings: WebSettingsStore | None = None,
    ) -> None:
        """一次发布完整运行时依赖，避免请求观察到部分初始化状态。"""

        self._build_agent = build_agent
        self._sessions = sessions
        self._approvals = approvals
        self._select_workspace = select_workspace
        self._settings = settings

    async def close(self) -> None:
        """关闭所有按需创建的 Agent，并清除运行时依赖。"""

        async with self._agents_lock:
            agents = tuple(self._agents.values())
            self._agents.clear()
            self._agent_settings.clear()
        await asyncio.gather(*(agent.close() for agent in agents))
        self._build_agent = None
        self._sessions = None
        self._approvals = None
        self._select_workspace = None
        self._settings = None

    async def require_agent(self, session_id: str, model_id: str | None = None) -> Agent:
        """返回绑定指定 Session 的唯一 Agent，必要时惰性创建。"""

        async with self._agents_lock:
            return await self._require_agent_locked(session_id, model_id)

    @asynccontextmanager
    async def agent_for_run(
        self, session_id: str, model_id: str | None = None
    ) -> AsyncIterator[Agent]:
        """模型切换到 Run 首事件交付前保持互斥，防止并发请求替换待启动实例。"""

        async with self._agents_lock:
            yield await self._require_agent_locked(session_id, model_id)

    async def _require_agent_locked(self, session_id: str, model_id: str | None) -> Agent:
        if self._build_agent is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        metadata = await self.require_sessions().get(session_id)
        if metadata is None:
            raise KeyError(f"会话不存在：{session_id}")
        _, settings = await self.require_settings().resolve(model_id or metadata.model_id)
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

    def find_agent(self, session_id: str) -> Agent | None:
        """返回已经创建的 Agent，不因控制命令隐式创建实例。"""

        return self._agents.get(session_id)

    async def update_session(
        self,
        session_id: str,
        *,
        name: str | None = None,
        archived: bool | None = None,
        model_id: str | None = None,
    ) -> SessionMetadata:
        """与 Run 启动互斥地校验并保存会话模型选择。"""

        async with self._agents_lock:
            if model_id is not None:
                agent = self._agents.get(session_id)
                if agent is not None and agent.is_running:
                    raise AgentBusyError("运行结束后才能切换会话模型")
                await self.require_settings().resolve(model_id)
            return await self.require_sessions().update(
                session_id, name=name, archived=archived, model_id=model_id
            )

    async def release_agent(self, session_id: str) -> bool:
        """移除空闲 Agent；活动 Run 存在时拒绝释放。"""

        async with self._agents_lock:
            agent = self._agents.get(session_id)
            if agent is None:
                return True
            if agent.is_running:
                return False
            self._agents.pop(session_id)
            self._agent_settings.pop(session_id, None)
        await agent.close()
        return True

    def require_sessions(self) -> SessionManager:
        """返回与当前 Agent 共用的会话管理器。"""

        if self._sessions is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._sessions

    def approvals(self) -> ApprovalCenter | None:
        """返回 Web 审批中心；当前入口未启用时返回空。"""

        return self._approvals

    def require_settings(self) -> WebSettingsStore:
        """返回 Web 本机模型配置。"""

        if self._settings is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._settings

    async def select_workspace(self) -> Path | None:
        """调用由 Backend 注入的平台目录选择器。"""

        if self._select_workspace is None:
            raise RuntimeError("当前入口未配置工作区选择器")
        return await self._select_workspace()
