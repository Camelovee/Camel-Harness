from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path

import yaml
from pydantic import ValidationError

from camel_backend.web_settings import ModelInput, Provider, WebSettingsStore
from camel_harness.agent import AgentBusyError
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.session import SessionManager

from .run_service import RunService


class SettingsService:
    """编排模型配置、全局 YAML 编辑与删除模型后的会话改绑。"""

    def __init__(
        self,
        store: WebSettingsStore,
        sessions: SessionManager,
        runs: RunService,
        config_path: Path | None = None,
    ) -> None:
        self._store = store
        self._sessions = sessions
        self._runs = runs
        self._config_path = config_path or Path.home() / ".camel" / "config.yaml"
        self._config_lock = asyncio.Lock()

    def _config_snapshot(self) -> dict[str, object]:
        """投影配置文件默认模型，不向浏览器回传密钥。"""

        if self._config_path.is_symlink():
            raise ValueError("配置文件不能是符号链接")
        settings = AgentSettings.load(self._config_path)
        anthropic = settings.provider == "anthropic"
        raw = self._config_path.read_bytes() if self._config_path.exists() else b""
        return {
            "path": str(self._config_path),
            "provider": settings.provider,
            "model": settings.model,
            "base_url": settings.anthropic_base_url if anthropic else settings.openai_base_url,
            "keys_configured": {
                "anthropic": bool(settings.anthropic_api_key),
                "openai": bool(settings.openai_api_key),
            },
            "revision": hashlib.sha256(raw).hexdigest(),
        }

    async def get_config(self) -> dict[str, object]:
        async with self._config_lock:
            try:
                return await asyncio.to_thread(self._config_snapshot)
            except OSError:
                raise RuntimeError("配置文件读取失败，请检查用户目录权限") from None

    async def save_config(
        self,
        *,
        revision: str,
        provider: Provider,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> dict[str, object]:
        """只更新默认模型字段，保留预算、存储目录及另一厂商配置。"""

        def save() -> dict[str, object]:
            if self._config_snapshot()["revision"] != revision:
                raise ValueError("配置文件已被修改，请重新加载后再保存")
            existing = AgentSettings.load(self._config_path)
            try:
                profile = ModelInput(
                    name="配置文件默认模型",
                    provider=provider,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    clear_api_key=clear_api_key,
                )
            except ValidationError:
                raise ValueError("模型配置无效，请检查模型 ID 和接口地址") from None
            values = existing.model_dump()
            values.update(provider=profile.provider, model=profile.model)
            values[f"{provider}_base_url"] = profile.base_url
            if clear_api_key:
                values[f"{provider}_api_key"] = None
            elif profile.api_key and profile.api_key.strip():
                values[f"{provider}_api_key"] = profile.api_key.strip()
            settings = AgentSettings.model_validate(values)
            self._config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self._config_path.parent,
                    prefix=".config-",
                    delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    yaml.safe_dump(
                        settings.model_dump(mode="json"),
                        stream,
                        allow_unicode=True,
                        sort_keys=False,
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self._config_path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return self._config_snapshot()

        async with self._config_lock:
            try:
                return await asyncio.to_thread(save)
            except OSError:
                raise RuntimeError("配置文件读写失败，请检查用户目录权限") from None

    async def delete_model(self, model_id: str) -> None:
        """删除模型配置并把使用它的会话改绑到新的默认模型。"""
        related = [item for item in await self._sessions.list() if item.model_id == model_id]
        if any(self._runs.is_running(item.session_id) for item in related):
            raise AgentBusyError("使用此模型的会话运行结束后才能删除")
        await self._store.delete_model(model_id)
        replacement = str((await self._store.snapshot())["default_model_id"])
        for metadata in related:
            await self._sessions.update(metadata.session_id, model_id=replacement)
