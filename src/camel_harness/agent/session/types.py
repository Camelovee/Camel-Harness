from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

SCHEMA_VERSION = 1


def _utc_now() -> str:
    """生成便于 JSON 持久化和字符串排序的 UTC 时间。"""

    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    """一个会话唯一对应的元数据。"""

    session_id: str
    name: str
    workspace: str
    created_at: str
    updated_at: str
    message_count: int = 0
    last_message_type: str | None = None
    model_id: str | None = None
    archived: bool = False
    parent_session_id: str | None = None
    parent_tool_call_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        session_id: str,
        name: str,
        workspace: str,
        *,
        parent_session_id: str | None = None,
        parent_tool_call_id: str | None = None,
    ) -> SessionMetadata:
        """创建带统一时间戳的新会话元数据。"""

        now = _utc_now()
        return cls(
            session_id=session_id,
            name=name.strip() or "新会话",
            workspace=workspace,
            created_at=now,
            updated_at=now,
            parent_session_id=parent_session_id,
            parent_tool_call_id=parent_tool_call_id,
        )

    def update(
        self,
        *,
        name: str | None = None,
        workspace: str | None = None,
        archived: bool | None = None,
        model_id: str | None = None,
    ) -> SessionMetadata:
        """返回更新后的不可变元数据，并保留创建时间。"""

        resolved_name = self.name if name is None else name.strip()
        if not resolved_name:
            raise ValueError("会话名称不能为空")
        return replace(
            self,
            name=resolved_name,
            workspace=self.workspace if workspace is None else workspace,
            archived=self.archived if archived is None else archived,
            model_id=self.model_id if model_id is None else model_id,
            updated_at=_utc_now(),
        )
