from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from camel_harness.agent.config import AgentSettings, ModelInput, ModelProfile
from camel_harness.llm import LlmModel


class SettingsStore:
    """以 ~/.camel/config.json 为唯一后端的模型与偏好存储；写入原子替换且不回显密钥。"""

    def __init__(self, config_path: Path | None = None) -> None:
        self._path = (config_path or Path.home() / ".camel" / "config.json").expanduser()
        if self._path.is_symlink():
            raise ValueError("配置文件不能是符号链接")
        AgentSettings.load(self._path)
        self._lock = asyncio.Lock()

    def _load(self) -> AgentSettings:
        """每次操作重读配置文件，手动修改后不需要重启后端。"""

        return AgentSettings.load(self._path)

    async def base_settings(self) -> AgentSettings:
        """返回 Agent 构建使用的非模型配置副本。"""

        async with self._lock:
            return self._load()

    async def resolve(self, model_id: str | None) -> LlmModel:
        """把模型选择解析为完整调用目标。"""

        async with self._lock:
            return self._load().resolve_model(model_id)

    async def snapshot(self) -> dict[str, object]:
        """返回不包含任何密钥内容的浏览器设置。"""

        async with self._lock:
            agent_settings = self._load()
            return {
                "models": [
                    {
                        "id": profile.id,
                        "name": profile.name,
                        "provider": profile.provider,
                        "model": profile.model,
                        "base_url": profile.base_url,
                        "configured": bool(profile.api_key),
                        "has_api_key": bool(profile.api_key),
                    }
                    for profile in agent_settings.models
                ],
                "default_model_id": agent_settings.default_model_id,
            }

    async def add_model(self, body: ModelInput) -> None:
        async with self._lock:
            agent_settings = self._load()
            profile = ModelProfile(
                id=uuid4().hex,
                name=body.name,
                provider=body.provider,
                model=body.model,
                base_url=body.base_url,
                api_key=body.api_key.strip() if body.api_key else None,
            )
            agent_settings.models.append(profile)
            # 尚无可用默认模型时，首个带密钥的模型自动成为默认。
            if not agent_settings.default_model_id and profile.api_key:
                agent_settings.default_model_id = profile.id
            agent_settings.save(self._path)

    async def update_model(self, model_id: str, body: ModelInput) -> None:
        async with self._lock:
            agent_settings = self._load()
            profile = next((item for item in agent_settings.models if item.id == model_id), None)
            if profile is None:
                raise KeyError("模型配置不存在")
            profile.name = body.name
            profile.provider = body.provider
            profile.model = body.model
            profile.base_url = body.base_url
            if body.clear_api_key:
                profile.api_key = None
            elif body.api_key and body.api_key.strip():
                profile.api_key = body.api_key.strip()
            agent_settings.save(self._path)

    async def delete_model(self, model_id: str) -> None:
        async with self._lock:
            agent_settings = self._load()
            remaining = [item for item in agent_settings.models if item.id != model_id]
            if len(remaining) == len(agent_settings.models):
                raise KeyError("模型配置不存在")
            agent_settings.models = remaining
            if agent_settings.default_model_id == model_id:
                agent_settings.default_model_id = next(
                    (item.id for item in agent_settings.models if item.api_key),
                    "",
                )
            agent_settings.save(self._path)

    async def update_preferences(self, default_model_id: str) -> None:
        async with self._lock:
            agent_settings = self._load()
            if default_model_id:
                profile = next(
                    (item for item in agent_settings.models if item.id == default_model_id), None
                )
                if profile is None:
                    raise ValueError("默认模型配置不存在")
                if not profile.api_key:
                    raise ValueError("默认模型缺少 API Key")
            agent_settings.default_model_id = default_model_id
            agent_settings.save(self._path)
