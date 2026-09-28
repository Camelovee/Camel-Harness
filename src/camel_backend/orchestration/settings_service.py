from __future__ import annotations

from camel_backend.web_settings import WebSettingsStore
from camel_harness.agent import AgentBusyError
from camel_harness.agent.session import SessionManager

from .run_service import RunService


class SettingsService:
    """模型配置写用例编排：删除模型时校验占用并把受影响会话改绑到默认模型。"""

    def __init__(
        self,
        store: WebSettingsStore,
        sessions: SessionManager,
        runs: RunService,
    ) -> None:
        self._store = store
        self._sessions = sessions
        self._runs = runs

    async def delete_model(self, model_id: str) -> None:
        """删除模型配置并把使用它的会话改绑到新的默认模型。"""
        related = [item for item in await self._sessions.list() if item.model_id == model_id]
        if any(self._runs.is_running(item.session_id) for item in related):
            raise AgentBusyError("使用此模型的会话运行结束后才能删除")
        await self._store.delete_model(model_id)
        replacement = str((await self._store.snapshot())["default_model_id"])
        for metadata in related:
            await self._sessions.update(metadata.session_id, model_id=replacement)
