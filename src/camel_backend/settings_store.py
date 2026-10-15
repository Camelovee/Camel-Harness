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
        """每次操作重读配置文件，手改或 CLI 写入不需要重启后端。"""

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
            settings = self._load()
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
                    for profile in settings.models
                ],
                "default_model_id": settings.default_model_id,
                "thinking": settings.thinking,
            }

    async def add_model(self, body: ModelInput) -> None:
        async with self._lock:
            state = self._load()
            profile = ModelProfile(
                id=uuid4().hex,
                name=body.name,
                provider=body.provider,
                model=body.model,
                base_url=body.base_url,
                api_key=body.api_key.strip() if body.api_key else None,
            )
            state.models.append(profile)
            # 尚无可用默认模型时，首个带密钥的模型自动成为默认。
            if not state.default_model_id and profile.api_key:
                state.default_model_id = profile.id
            state.save(self._path)

    async def update_model(self, model_id: str, body: ModelInput) -> None:
        async with self._lock:
            state = self._load()
            profile = next((item for item in state.models if item.id == model_id), None)
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
            state.save(self._path)

    async def delete_model(self, model_id: str) -> None:
        async with self._lock:
            state = self._load()
            remaining = [item for item in state.models if item.id != model_id]
            if len(remaining) == len(state.models):
                raise KeyError("模型配置不存在")
            state.models = remaining
            if state.default_model_id == model_id:
                state.default_model_id = next(
                    (item.id for item in state.models if item.api_key),
                    "",
                )
            state.save(self._path)

    async def update_preferences(self, default_model_id: str, thinking: bool) -> None:
        async with self._lock:
            state = self._load()
            if default_model_id:
                profile = next((item for item in state.models if item.id == default_model_id), None)
                if profile is None:
                    raise ValueError("默认模型配置不存在")
                if not profile.api_key:
                    raise ValueError("默认模型缺少 API Key")
            state.default_model_id = default_model_id
            state.thinking = thinking
            state.save(self._path)
