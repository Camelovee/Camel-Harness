from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from camel_backend.approval import WebApproval
from camel_backend.orchestration import RunService, SessionService, SettingsService
from camel_backend.settings_store import SettingsStore
from camel_harness.agent.session import SessionManager

WorkspacePicker = Callable[[], Awaitable[Path | None]]


@dataclass(slots=True)
class HttpDependencies:
    """保存共享应用服务；路由只能通过它访问运行时依赖。"""

    workspace: Path
    _run_service: RunService | None = field(default=None, init=False, repr=False)
    _session_manager: SessionManager | None = field(default=None, init=False, repr=False)
    _session_service: SessionService | None = field(default=None, init=False, repr=False)
    _settings_service: SettingsService | None = field(default=None, init=False, repr=False)
    _settings_store: SettingsStore | None = field(default=None, init=False, repr=False)
    _web_approval: WebApproval | None = field(default=None, init=False, repr=False)
    _select_workspace: WorkspacePicker | None = field(default=None, init=False, repr=False)

    def bind(
        self,
        *,
        run_service: RunService,
        session_manager: SessionManager,
        session_service: SessionService,
        settings_service: SettingsService,
        settings_store: SettingsStore,
        web_approval: WebApproval | None = None,
        select_workspace: WorkspacePicker | None = None,
    ) -> None:
        """一次发布完整运行时依赖，避免请求观察到部分初始化状态。"""

        self._run_service = run_service
        self._session_manager = session_manager
        self._session_service = session_service
        self._settings_service = settings_service
        self._settings_store = settings_store
        self._web_approval = web_approval
        self._select_workspace = select_workspace

    async def close(self) -> None:
        """关闭所有按需创建的 Agent，并清除运行时依赖。"""

        run_service = self._run_service
        self._run_service = None
        self._session_manager = None
        self._session_service = None
        self._settings_service = None
        self._settings_store = None
        self._web_approval = None
        self._select_workspace = None
        if run_service is not None:
            await run_service.close()

    def require_runs(self) -> RunService:
        """返回 Agent 运行编排服务。"""

        if self._run_service is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._run_service

    def require_sessions(self) -> SessionManager:
        """返回与当前 Agent 共用的会话管理器。"""

        if self._session_manager is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._session_manager

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

    def require_settings(self) -> SettingsStore:
        """返回 Web 本机模型配置。"""

        if self._settings_store is None:
            raise RuntimeError("Camel Harness Web 运行时尚未完成初始化")
        return self._settings_store

    def approvals(self) -> WebApproval | None:
        """返回 Web 审批中心；当前入口未启用时返回空。"""

        return self._web_approval

    async def select_workspace(self) -> Path | None:
        """调用由 Backend 注入的平台目录选择器。"""

        if self._select_workspace is None:
            raise RuntimeError("当前入口未配置工作区选择器")
        return await self._select_workspace()
