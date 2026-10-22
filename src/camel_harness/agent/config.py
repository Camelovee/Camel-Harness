from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from camel_harness.llm import LlmModel, LlmThinkingMode

Provider = Literal["anthropic", "openai"]


class CompactionSettings(BaseModel):
    """独立的压缩策略与摘要预算；窗口大小需与所选模型匹配。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    enabled: bool = True
    reserve_tokens: int = Field(default=16_384, gt=0)
    keep_recent_tokens: int = Field(default=20_000, gt=0)
    min_messages: int = Field(default=8, ge=2)
    max_output_tokens: int = Field(default=8192, gt=0)
    retry_max_output_tokens: int = Field(default=16_384, gt=0)

    @model_validator(mode="after")
    def validate_budgets(self) -> CompactionSettings:
        if self.retry_max_output_tokens < self.max_output_tokens:
            raise ValueError("压缩重试预算不能小于首次摘要预算")
        return self


class MemorySettings(BaseModel):
    """长期记忆的自动注入预算，与压缩及回答预算相互独立。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    enabled: bool = True
    max_entries: int = Field(default=5, gt=0)
    context_tokens: int = Field(default=2000, gt=0)


class ModelCapabilities(BaseModel):
    """模型能力由用户显式确认，不根据模型名称或接口地址推测。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    provider: Provider
    context_window_tokens: int = Field(default=128_000, gt=0)
    max_output_tokens: int = Field(default=16_384, gt=0)
    thinking_mode: LlmThinkingMode = LlmThinkingMode.PROVIDER_DEFAULT

    @model_validator(mode="after")
    def validate_capabilities(self) -> ModelCapabilities:
        if self.context_window_tokens <= self.max_output_tokens + 4096:
            raise ValueError("模型窗口必须大于输出预算与 4096 Token 安全余量之和")
        if (
            self.thinking_mode is not LlmThinkingMode.PROVIDER_DEFAULT
            and self.thinking_mode.value.split("_")[0] != self.provider
        ):
            raise ValueError("思考模式与 Provider 不匹配")
        return self


class ModelProfile(ModelCapabilities):
    """持久化的模型配置；密钥仅存在于本机配置文件。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    id: str
    name: str
    model: str
    base_url: str | None = None
    api_key: str | None = Field(default=None, repr=False)


class ModelInput(ModelCapabilities):
    """用户可编辑的模型连接参数；空密钥表示更新时保留旧值。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=80)
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


class AgentSettings(BaseModel):
    """应用配置；仅从用户级 JSON 显式加载，默认使用 Anthropic。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    compaction: CompactionSettings = Field(default_factory=CompactionSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    max_duration_seconds: float = Field(default=900, gt=0)
    max_steering_messages: int = Field(default=32, gt=0)
    max_steering_chars: int = Field(default=100_000, gt=0)
    session_dir: Path = Field(default_factory=lambda: Path.home() / ".camel" / "sessions")
    mcp_config: str = ".mcp.json"
    models: list[ModelProfile] = Field(default_factory=list)
    default_model_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def migrate_model_budgets(cls, value: Any) -> Any:
        """仅在读取边界迁移旧全局字段；保存不再产生旧字段。"""
        if not isinstance(value, dict):
            return value
        settings_data = dict(value)
        legacy_output = TypeAdapter(PositiveInt).validate_python(
            settings_data.pop("max_output_tokens", 16_384)
        )
        compaction_data = settings_data.get("compaction", {})
        legacy_window = 128_000
        if isinstance(compaction_data, dict):
            compaction_data = dict(compaction_data)
            legacy_window = TypeAdapter(PositiveInt).validate_python(
                compaction_data.pop("context_window_tokens", 128_000)
            )
            settings_data["compaction"] = compaction_data
        profiles = settings_data.get("models", [])
        if isinstance(profiles, list):
            settings_data["models"] = [
                {
                    "max_output_tokens": legacy_output,
                    "context_window_tokens": legacy_window,
                    **profile,
                }
                if isinstance(profile, dict)
                else profile
                for profile in profiles
            ]
        return settings_data

    @classmethod
    def load(cls, path: Path | None = None) -> AgentSettings:
        """入口读取配置；缺失时创建默认文件，错误不回显含密钥的原文。"""

        config_path = path or Path.home() / ".camel" / "config.json"
        config_path = config_path.expanduser()
        try:
            if not config_path.exists():
                cls._create_default(config_path)
            with config_path.open(encoding="utf-8") as stream:
                settings_data = json.load(stream)
        except (json.JSONDecodeError, UnicodeError):
            raise ValueError(f"配置文件 JSON 格式错误：{config_path}") from None
        except OSError:
            raise ValueError(f"配置文件无法创建或读取：{config_path}") from None
        if not isinstance(settings_data, dict):
            raise ValueError(f"配置文件顶层必须是字段映射：{config_path}")
        try:
            return cls.model_validate(settings_data)
        except ValidationError as exc:
            fields = ", ".join(
                ".".join(map(str, error["loc"])) for error in exc.errors(include_input=False)
            )
            raise ValueError(f"配置字段无效：{fields}（{config_path}）") from None

    @classmethod
    def _create_default(cls, path: Path) -> None:
        """完整写入后独占发布，避免并发启动覆盖配置或读到半份 JSON。"""

        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".config-",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(cls().model_dump(mode="json"), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                # 另一个启动入口已经发布配置，加载它而不是覆盖。
                pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def resolved_session_dir(self) -> Path:
        """解析用户级会话目录；相对配置也固定相对于用户主目录。"""

        configured = self.session_dir.expanduser()
        if not configured.is_absolute():
            configured = Path.home() / configured
        return configured.resolve()

    def save(self, path: Path | None = None) -> None:
        """0600 权限原子替换写回配置文件；密钥不进入日志。"""

        config_path = (path or Path.home() / ".camel" / "config.json").expanduser()
        config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=config_path.parent,
                prefix=".config-",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(self.model_dump(mode="json"), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, config_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def resolve_model(self, model_id: str | None) -> LlmModel:
        """把模型选择解析为完整调用目标；缺资料或缺密钥在入口边界报错。"""

        selected = model_id or self.default_model_id
        if not selected:
            raise ValueError("请先在设置中添加模型")
        profile = next((item for item in self.models if item.id == selected), None)
        if profile is None:
            raise ValueError("所选模型配置不存在，请重新选择")
        if not profile.api_key:
            raise ValueError("所选模型缺少 API Key，请在设置中填写")
        return LlmModel(
            profile.provider,
            profile.model,
            api_key=profile.api_key,
            base_url=profile.base_url,
            context_window_tokens=profile.context_window_tokens,
            max_output_tokens=profile.max_output_tokens,
            thinking_mode=profile.thinking_mode,
        )
