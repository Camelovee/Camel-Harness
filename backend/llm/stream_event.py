from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class LlmStreamEventType(StrEnum):
    """所有 Provider 必须映射到的规范流式事件。"""

    STREAM_STARTED = "stream_started"
    TEXT_DELTA = "text_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_ARGUMENTS_DELTA = "tool_call_arguments_delta"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    USAGE_UPDATED = "usage_updated"
    MESSAGE_COMPLETED = "message_completed"
    STREAM_FAILED = "stream_failed"
    STREAM_CANCELLED = "stream_cancelled"


@dataclass(frozen=True, slots=True)
class LlmStreamEvent:
    """用于 Agent、Session 和 Trigger 之间传递的规范增量事件。"""

    type: LlmStreamEventType
    session_id: str
    turn_id: str
    step_id: str
    sequence: int
    provider: str
    data: dict[str, Any] = field(default_factory=dict)
