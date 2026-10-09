from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from camel_harness.agent.config import AgentSettings

Provider = Literal["anthropic", "openai"]
# 默认模型的持久化 ID 保持稳定，配置来源由 JSON 加载器决定。
DEFAULT_MODEL_ID = "environment"


class ModelInput(BaseModel):
    """用户可编辑的模型连接参数；空密钥表示更新时保留旧值。"""

    name: str = Field(min_length=1, max_length=80)
    provider: Provider
    model: str = Field(min_length=1, max_length=160)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=4_096)
    clear_api_key: bool = False

    @field_validator("name", "model")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("名称和模型 ID 不能为空")
        return value

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("接口地址必须以 http:// 或 https:// 开头")
        return value


class ModelProfile(BaseModel):
    """持久化的模型配置；密钥仅存在于后端本机文件。"""

    id: str
    name: str
    provider: Provider
    model: str
    base_url: str | None = None
    api_key: str | None = None


class StoredWebSettings(BaseModel):
    """本地 Web 偏好与自定义模型列表。"""

    models: list[ModelProfile] = Field(default_factory=list)
    default_model_id: str = DEFAULT_MODEL_ID
    thinking: bool = False


class WebSettingsStore:
    """管理本机 Web 配置，并以原子替换和 0600 权限保存密钥。"""

    def __init__(self, path: Path, defaults: AgentSettings) -> None:
        self._path = path.expanduser()
        self._defaults = defaults
        self._lock = asyncio.Lock()
        self._state = (
            StoredWebSettings.model_validate_json(self._path.read_text("utf-8"))
            if self._path.exists()
            else StoredWebSettings()
        )

    async def snapshot(self) -> dict[str, object]:
        """返回不包含任何密钥内容的浏览器设置。"""

        async with self._lock:
            models = [
                {
                    "id": DEFAULT_MODEL_ID,
                    "name": "配置文件默认模型",
                    "provider": self._defaults.provider,
                    "model": self._defaults.model,
                    "base_url": self._defaults.anthropic_base_url
                    if self._defaults.provider == "anthropic"
                    else self._defaults.openai_base_url,
                    "configured": bool(self._defaults_key(self._defaults.provider)),
                    "has_api_key": False,
                    "read_only": True,
                }
            ]
            for profile in self._state.models:
                models.append(
                    {
                        "id": profile.id,
                        "name": profile.name,
                        "provider": profile.provider,
                        "model": profile.model,
                        "base_url": profile.base_url,
                        "configured": bool(profile.api_key or self._defaults_key(profile.provider)),
                        "has_api_key": bool(profile.api_key),
                        "read_only": False,
                    }
                )
            return {
                "models": models,
                "default_model_id": self._state.default_model_id,
                "thinking": self._state.thinking,
            }

    async def resolve(self, model_id: str | None) -> tuple[str, AgentSettings]:
        """解析一次新 Run 要使用的模型与 Provider 凭据。"""

        async with self._lock:
            selected = model_id or self._state.default_model_id
            if selected == DEFAULT_MODEL_ID:
                if not self._defaults_key(self._defaults.provider):
                    raise ValueError("默认模型缺少 API Key，请在设置中配置模型")
                return selected, self._defaults.model_copy(deep=True)
            profile = next((item for item in self._state.models if item.id == selected), None)
            if profile is None:
                raise ValueError("所选模型配置不存在，请重新选择")
            key = profile.api_key or self._defaults_key(profile.provider)
            if not key:
                raise ValueError("所选模型缺少 API Key，请在设置中填写")
            updates: dict[str, object] = {
                "provider": profile.provider,
                "model": profile.model,
            }
            if profile.provider == "anthropic":
                updates["anthropic_api_key"] = key
                updates["anthropic_base_url"] = (
                    profile.base_url or self._defaults.anthropic_base_url
                )
            else:
                updates["openai_api_key"] = key
                updates["openai_base_url"] = profile.base_url or self._defaults.openai_base_url
            return selected, self._defaults.model_copy(update=updates)

    async def add_model(self, body: ModelInput) -> None:
        async with self._lock:
            state = self._state.model_copy(deep=True)
            profile = ModelProfile(
                id=uuid4().hex,
                name=body.name,
                provider=body.provider,
                model=body.model,
                base_url=body.base_url,
                api_key=body.api_key.strip() if body.api_key else None,
            )
            state.models.append(profile)
            if (
                state.default_model_id == DEFAULT_MODEL_ID
                and not self._defaults_key(self._defaults.provider)
                and (profile.api_key or self._defaults_key(profile.provider))
            ):
                state.default_model_id = profile.id
            self._save(state)

    async def update_model(self, model_id: str, body: ModelInput) -> None:
        async with self._lock:
            state = self._state.model_copy(deep=True)
            profile = next((item for item in state.models if item.id == model_id), None)
            if profile is None:
                raise KeyError("模型配置不存在")
            provider_changed = profile.provider != body.provider
            profile.name = body.name
            profile.provider = body.provider
            profile.model = body.model
            profile.base_url = body.base_url
            if body.clear_api_key or (provider_changed and not body.api_key):
                profile.api_key = None
            elif body.api_key and body.api_key.strip():
                profile.api_key = body.api_key.strip()
            self._save(state)

    async def delete_model(self, model_id: str) -> None:
        async with self._lock:
            state = self._state.model_copy(deep=True)
            state.models = [item for item in state.models if item.id != model_id]
            if len(state.models) == len(self._state.models):
                raise KeyError("模型配置不存在")
            if state.default_model_id == model_id:
                state.default_model_id = next(
                    (
                        item.id
                        for item in state.models
                        if item.api_key or self._defaults_key(item.provider)
                    ),
                    DEFAULT_MODEL_ID,
                )
            self._save(state)

    async def update_preferences(self, default_model_id: str, thinking: bool) -> None:
        async with self._lock:
            state = self._state.model_copy(deep=True)
            if default_model_id != DEFAULT_MODEL_ID and not any(
                item.id == default_model_id for item in state.models
            ):
                raise ValueError("默认模型配置不存在")
            if default_model_id == DEFAULT_MODEL_ID:
                configured = bool(self._defaults_key(self._defaults.provider))
            else:
                profile = next(item for item in state.models if item.id == default_model_id)
                configured = bool(profile.api_key or self._defaults_key(profile.provider))
            if not configured and default_model_id != state.default_model_id:
                raise ValueError("默认模型缺少 API Key")
            state.default_model_id = default_model_id
            state.thinking = thinking
            self._save(state)

    def _defaults_key(self, provider: Provider) -> str | None:
        return (
            self._defaults.anthropic_api_key
            if provider == "anthropic"
            else self._defaults.openai_api_key
        )

    def _save(self, state: StoredWebSettings) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=".web-settings-",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(state.model_dump(), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
            self._state = state
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
