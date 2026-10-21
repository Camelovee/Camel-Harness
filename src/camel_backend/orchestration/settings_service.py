from __future__ import annotations

from camel_backend.settings_store import SettingsStore
from camel_harness.agent import AgentBusyError
from camel_harness.agent.session import SessionManager

from .run_service import RunService


class SettingsService:
    """编排删除模型后的会话改绑；配置读写由 SettingsStore 承担。"""

    def __init__(
        self,
        settings_store: SettingsStore,
        session_manager: SessionManager,
        run_service: RunService,
    ) -> None:
        self._settings_store = settings_store
        self._session_manager = session_manager
        self._run_service = run_service

    async def delete_model(self, model_id: str) -> None:
        """删除模型配置并把使用它的会话改绑到新的默认模型。"""
        async with self._run_service.mutation():
            related = [
                item for item in await self._session_manager.list() if item.model_id == model_id
            ]
            if any(self._run_service.is_running(item.session_id) for item in related):
                raise AgentBusyError("使用此模型的会话运行结束后才能删除")
            await self._settings_store.delete_model(model_id)
            replacement = str((await self._settings_store.snapshot())["default_model_id"])
            for metadata in related:
                await self._run_service.release_idle_locked(metadata.session_id)
                await self._session_manager.update(metadata.session_id, model_id=replacement)
