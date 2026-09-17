from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from backend.llm import LlmStreamEvent


class AgentStreamEventType(StrEnum):
    """Agent Runtime 对外产生的规范流事件类型。"""

    STREAM_STARTED = "stream_started"
    TEXT_DELTA = "text_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_ARGUMENTS_DELTA = "tool_call_arguments_delta"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    APPROVAL_REQUESTED = "approval_requested"
    TOOL_RESULT = "tool_result"
    USAGE_UPDATED = "usage_updated"
    MESSAGE_COMPLETED = "message_completed"
    STEP_COMPLETED = "step_completed"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    TURN_CANCELLED = "turn_cancelled"
    STREAM_FAILED = "stream_failed"
    STREAM_CANCELLED = "stream_cancelled"


@dataclass(frozen=True, slots=True)
class AgentStreamEvent:
    """跨 Step 输出的 Agent 事件，不向 LLM 上下文泄漏工具运行时语义。"""

    type: AgentStreamEventType
    session_id: str
    turn_id: str
    step_id: str
    sequence: int
    source: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_llm(cls, event: LlmStreamEvent) -> AgentStreamEvent:
        """把一次 Provider 增量提升为 Agent 运行时事件。"""

        return cls(
            type=AgentStreamEventType(event.type.value),
            session_id=event.session_id,
            turn_id=event.turn_id,
            step_id=event.step_id,
            sequence=event.sequence,
            source=event.provider,
            data=event.data,
        )
