from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from camel_backend.approval import WebApproval
from camel_backend.orchestration import RunService, SessionService, SettingsService
from camel_backend.web_settings import WebSettingsStore
from camel_harness.agent.session import SessionManager

WorkspacePicker = Callable[[], Awaitable[Path | None]]


@dataclass(slots=True)
class HttpDependencies:
    """保存共享应用服务；路由只能通过它访问运行时依赖。"""

    workspace: Path
    _runs: RunService | None = field(default=None, init=False, repr=False)
    _sessions: SessionManager | None = field(default=None, init=False, repr=False)
    _session_service: SessionService | None = field(default=None, init=False, repr=False)
    _settings_service: SettingsService | None = field(default=None, init=False, repr=False)
    _settings: WebSettingsStore | None = field(default=None, init=False, repr=False)
    _approvals: WebApproval | None = field(default=None, init=False, repr=False)
    _select_workspace: WorkspacePicker | None = field(default=None, init=False, repr=False)

    def bind(
        self,
        *,
        runs: RunService,
        sessions: SessionManager,
        session_service: SessionService,
        settings_service: SettingsService,
        settings: WebSettingsStore,
        approvals: WebApproval | None = None,
        select_workspace: WorkspacePicker | None = None,
    ) -> None:
        """一次发布完整运行时依赖，避免请求观察到部分初始化状态。"""

        self._runs = runs
        self._sessions = sessions
        self._session_service = session_service
        self._settings_service = settings_service
        self._settings = settings
        self._approvals = approvals
        self._select_workspace = select_workspace

    async def close(self) -> None:
        """关闭所有按需创建的 Agent，并清除运行时依赖。"""

        runs = self._runs
        self._runs = None
        self._sessions = None
        self._session_service = None
        self._settings_service = None
        self._settings = None
        self._approvals = None
        self._select_workspace = None
        if runs is not None:
            await runs.close()

    def require_runs(self) -> RunService:
        """返回 Agent 运行编排服务。"""

        if self._runs is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._runs

    def require_sessions(self) -> SessionManager:
        """返回与当前 Agent 共用的会话管理器。"""

        if self._sessions is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._sessions

    def require_session_service(self) -> SessionService:
        """返回会话写用例编排服务。"""

        if self._session_service is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._session_service

    def require_settings_service(self) -> SettingsService:
        """返回模型配置写用例编排服务。"""

        if self._settings_service is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._settings_service

    def require_settings(self) -> WebSettingsStore:
        """返回 Web 本机模型配置。"""

        if self._settings is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._settings

    def approvals(self) -> WebApproval | None:
        """返回 Web 审批中心；当前入口未启用时返回空。"""

        return self._approvals

    async def select_workspace(self) -> Path | None:
        """调用由 Backend 注入的平台目录选择器。"""

        if self._select_workspace is None:
            raise RuntimeError("当前入口未配置工作区选择器")
        return await self._select_workspace()
