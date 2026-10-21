from __future__ import annotations

from camel_backend.settings_store import SettingsStore
from camel_harness.agent import AgentBusyError
from camel_harness.agent.session import SessionManager, SessionMetadata

from .run_service import RunService


class SessionService:
    """会话写用例编排：在仓储之上补充与 Agent 生命周期相关的业务规则。"""

    def __init__(
        self,
        session_manager: SessionManager,
        run_service: RunService,
        settings_store: SettingsStore,
    ) -> None:
        self._session_manager = session_manager
        self._run_service = run_service
        self._settings_store = settings_store

    async def update(
        self,
        session_id: str,
        *,
        name: str | None = None,
        archived: bool | None = None,
        model_id: str | None = None,
    ) -> SessionMetadata:
        """与 Run 启动互斥地校验并保存会话元数据变更。"""
        async with self._run_service.mutation():
            if model_id is not None:
                if self._run_service.is_running(session_id):
                    raise AgentBusyError("运行结束后才能切换会话模型")
                await self._settings_store.resolve(model_id)
            return await self._session_manager.update(
                session_id, name=name, archived=archived, model_id=model_id
            )

    async def delete_cascade(self, session_id: str) -> None:
        """释放会话及其后代的 Agent 后级联删除；活动 Run 存在时拒绝。"""
        async with self._run_service.mutation():
            order = await self._session_manager.deletion_order(session_id)
            if any(self._run_service.is_running(current_id) for current_id in order):
                raise AgentBusyError("活动 Run 结束前不能删除会话")
            for current_id in order:
                if not await self._run_service.release_idle_locked(current_id):
                    raise AgentBusyError("活动 Run 结束前不能删除会话")
            if not await self._session_manager.delete(session_id):
                raise KeyError("会话不存在")
