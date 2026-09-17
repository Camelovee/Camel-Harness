from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HarnessSettings(BaseSettings):
    """应用配置；默认使用 Anthropic Provider。"""

    model_config = SettingsConfigDict(
        env_prefix="CAMEL_", env_file=".env", extra="ignore", populate_by_name=True
    )

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
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "CAMEL_ANTHROPIC_API_KEY"),
    )
    anthropic_base_url: str | None = None
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "CAMEL_OPENAI_API_KEY"),
    )
    openai_base_url: str | None = None

    def resolved_session_dir(self) -> Path:
        """解析用户级会话目录；相对配置也固定相对于用户主目录。"""

        configured = self.session_dir.expanduser()
        if not configured.is_absolute():
            configured = Path.home() / configured
        return configured.resolve()
