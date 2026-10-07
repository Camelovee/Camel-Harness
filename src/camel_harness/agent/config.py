from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class AgentSettings(BaseModel):
    """应用配置；仅从用户级 YAML 显式加载，默认使用 Anthropic。"""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    provider: Literal["anthropic", "openai"] = "anthropic"
    model: str = "claude-sonnet-4-6"
    max_output_tokens: int = 4096
    max_context_chars: int = 120_000
    compaction_min_messages: int = 8
    max_compaction_tokens: int = 2048
    max_total_tokens: int = 200_000
    max_duration_seconds: float = 900
    session_dir: Path = Field(default_factory=lambda: Path.home() / ".camel" / "sessions")
    mcp_config: str = ".mcp.json"
    anthropic_api_key: str | None = Field(default=None, repr=False)
    anthropic_base_url: str | None = None
    openai_api_key: str | None = Field(default=None, repr=False)
    openai_base_url: str | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> AgentSettings:
        """入口读取配置；缺失时创建默认文件，错误不回显含密钥的原文。"""

        config_path = path or Path.home() / ".camel" / "config.yaml"
        config_path = config_path.expanduser()
        try:
            if not config_path.exists():
                cls._create_default(config_path)
            with config_path.open(encoding="utf-8") as stream:
                data = yaml.safe_load(stream)
        except (yaml.YAMLError, UnicodeError):
            raise ValueError(f"配置文件 YAML 格式错误：{config_path}") from None
        except OSError:
            raise ValueError(f"配置文件无法创建或读取：{config_path}") from None
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"配置文件顶层必须是字段映射：{config_path}")
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            fields = ", ".join(
                ".".join(map(str, error["loc"])) for error in exc.errors(include_input=False)
            )
            raise ValueError(f"配置字段无效：{fields}（{config_path}）") from None

    @classmethod
    def _create_default(cls, path: Path) -> None:
        """完整写入后独占发布，避免并发启动覆盖配置或读到半份 YAML。"""

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
                yaml.safe_dump(cls().model_dump(mode="json"), stream, sort_keys=False)
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
