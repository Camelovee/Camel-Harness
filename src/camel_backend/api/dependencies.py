from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from camel_harness.agent import Agent
from camel_harness.agent.session import SessionManager
from camel_harness.agent.tools import ApprovalCenter

WorkspacePicker = Callable[[], Awaitable[Path | None]]


@dataclass(slots=True)
class HttpDependencies:
    """保存入口默认工作区，并原子发布启动期创建的 Agent。"""

    workspace: Path
    _agent: Agent | None = field(default=None, init=False, repr=False)
    _sessions: SessionManager | None = field(default=None, init=False, repr=False)
    _approvals: ApprovalCenter | None = field(default=None, init=False, repr=False)
    _select_workspace: WorkspacePicker | None = field(default=None, init=False, repr=False)

    def bind(
        self,
        agent: Agent,
        sessions: SessionManager,
        approvals: ApprovalCenter | None = None,
        select_workspace: WorkspacePicker | None = None,
    ) -> None:
        """一次发布完整 Agent，避免请求观察到部分初始化状态。"""

        self._agent = agent
        self._sessions = sessions
        self._approvals = approvals
        self._select_workspace = select_workspace

    def clear(self) -> None:
        """在应用停止后清除已关闭的运行时依赖。"""

        self._agent = None
        self._sessions = None
        self._approvals = None
        self._select_workspace = None

    def require(self) -> Agent:
        """返回已装配 Agent；应用尚未就绪时快速失败。"""

        if self._agent is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._agent

    def require_sessions(self) -> SessionManager:
        """返回与当前 Agent 共用的会话管理器。"""

        if self._sessions is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._sessions

    def approvals(self) -> ApprovalCenter | None:
        """返回 Web 审批中心；当前入口未启用时返回空。"""

        return self._approvals

    async def select_workspace(self) -> Path | None:
        """调用由 Backend 注入的平台目录选择器。"""

        if self._select_workspace is None:
            raise RuntimeError("当前入口未配置工作区选择器")
        return await self._select_workspace()
