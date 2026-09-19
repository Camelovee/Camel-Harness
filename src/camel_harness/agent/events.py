from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from camel_harness.llm import AssistantMessage, LlmStreamEvent, Message, ToolResultMessage

from .state import AgentRun, StopReason
from .tools.permissions import PermissionRequest
from .tools.types import ToolCall, ToolResult

MessageKind: TypeAlias = Literal["user", "assistant", "toolResult"]
AgentEndStatus: TypeAlias = Literal["completed", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class AgentStartEvent:
    """一次 Agent 执行已开始。"""

    session_id: str
    run_id: str
    sequence: int
    type: Literal["agent_start"] = field(default="agent_start", init=False)


@dataclass(frozen=True, slots=True)
class AgentEndEvent:
    """一次 Agent 执行已进入唯一终态。"""

    session_id: str
    run_id: str
    sequence: int
    status: AgentEndStatus
    stop_reason: StopReason | None
    total_tokens: int
    error: str | None = None
    type: Literal["agent_end"] = field(default="agent_end", init=False)


@dataclass(frozen=True, slots=True)
class TurnStartEvent:
    """一次 assistant 响应及其工具阶段已开始。"""

    session_id: str
    run_id: str
    sequence: int
    turn_id: str
    turn: int
    type: Literal["turn_start"] = field(default="turn_start", init=False)


@dataclass(frozen=True, slots=True)
class TurnEndEvent:
    """一次 assistant 响应及其工具阶段已结束。"""

    session_id: str
    run_id: str
    sequence: int
    turn_id: str
    message: AssistantMessage
    tool_results: tuple[ToolResultMessage, ...]
    type: Literal["turn_end"] = field(default="turn_end", init=False)


@dataclass(frozen=True, slots=True)
class MessageStartEvent:
    """一条用户、assistant 或工具结果消息已开始。"""

    session_id: str
    run_id: str
    sequence: int
    turn_id: str
    message_id: str
    role: MessageKind
    type: Literal["message_start"] = field(default="message_start", init=False)


@dataclass(frozen=True, slots=True)
class MessageUpdateEvent:
    """assistant 消息的一个厂商无关增量。"""

    session_id: str
    run_id: str
    sequence: int
    turn_id: str
    message_id: str
    assistant_event: LlmStreamEvent
    type: Literal["message_update"] = field(default="message_update", init=False)


@dataclass(frozen=True, slots=True)
class MessageEndEvent:
    """一条规范消息已完整生成并可以持久化。"""

    session_id: str
    run_id: str
    sequence: int
    turn_id: str
    message_id: str
    message: Message
    type: Literal["message_end"] = field(default="message_end", init=False)


@dataclass(frozen=True, slots=True)
class ToolExecutionStartEvent:
    """工具调用已进入实际执行阶段。"""

    session_id: str
    run_id: str
    sequence: int
    turn_id: str
    call_id: str
    name: str
    arguments: dict[str, Any]
    type: Literal["tool_execution_start"] = field(default="tool_execution_start", init=False)


@dataclass(frozen=True, slots=True)
class ToolExecutionEndEvent:
    """工具调用已归一化为最终结果。"""

    session_id: str
    run_id: str
    sequence: int
    turn_id: str
    result: ToolResult
    type: Literal["tool_execution_end"] = field(default="tool_execution_end", init=False)


@dataclass(frozen=True, slots=True)
class ApprovalRequestedEvent:
    """工具调用已挂起并等待用户审批。"""

    session_id: str
    run_id: str
    sequence: int
    turn_id: str
    request_id: str
    call_id: str
    name: str
    capabilities: tuple[str, ...]
    risk: str
    arguments: dict[str, Any]
    reason: str
    type: Literal["approval_requested"] = field(default="approval_requested", init=False)


AgentEvent: TypeAlias = (
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionEndEvent
    | ApprovalRequestedEvent
)


def agent_start_event(run: AgentRun) -> AgentStartEvent:
    return AgentStartEvent(run.session_id, run.run_id, run.next_sequence())


def agent_end_event(run: AgentRun, error: str | None = None) -> AgentEndEvent:
    status: AgentEndStatus
    if run.status.value == "completed":
        status = "completed"
    elif run.status.value == "cancelled":
        status = "cancelled"
    else:
        status = "failed"
    return AgentEndEvent(
        run.session_id,
        run.run_id,
        run.next_sequence(),
        status,
        run.stop_reason,
        run.total_tokens,
        error,
    )


def turn_start_event(run: AgentRun, turn_id: str) -> TurnStartEvent:
    return TurnStartEvent(
        run.session_id,
        run.run_id,
        run.next_sequence(),
        turn_id,
        run.turn,
    )


def turn_end_event(
    run: AgentRun,
    turn_id: str,
    message: AssistantMessage,
    tool_results: tuple[ToolResultMessage, ...],
) -> TurnEndEvent:
    return TurnEndEvent(
        run.session_id,
        run.run_id,
        run.next_sequence(),
        turn_id,
        message,
        tool_results,
    )


def message_start_event(
    run: AgentRun,
    turn_id: str,
    message_id: str,
    role: MessageKind,
) -> MessageStartEvent:
    return MessageStartEvent(
        run.session_id,
        run.run_id,
        run.next_sequence(),
        turn_id,
        message_id,
        role,
    )


def message_update_event(
    run: AgentRun,
    turn_id: str,
    message_id: str,
    event: LlmStreamEvent,
) -> MessageUpdateEvent:
    return MessageUpdateEvent(
        run.session_id,
        run.run_id,
        run.next_sequence(),
        turn_id,
        message_id,
        event,
    )


def message_end_event(
    run: AgentRun,
    turn_id: str,
    message_id: str,
    message: Message,
) -> MessageEndEvent:
    return MessageEndEvent(
        run.session_id,
        run.run_id,
        run.next_sequence(),
        turn_id,
        message_id,
        message,
    )


def tool_execution_start_event(
    run: AgentRun,
    turn_id: str,
    call: ToolCall,
) -> ToolExecutionStartEvent:
    return ToolExecutionStartEvent(
        run.session_id,
        run.run_id,
        run.next_sequence(),
        turn_id,
        call.call_id,
        call.name,
        call.arguments,
    )


def tool_execution_end_event(
    run: AgentRun,
    turn_id: str,
    result: ToolResult,
) -> ToolExecutionEndEvent:
    return ToolExecutionEndEvent(
        run.session_id,
        run.run_id,
        run.next_sequence(),
        turn_id,
        result,
    )


def approval_requested_event(
    run: AgentRun,
    turn_id: str,
    call: ToolCall,
    request_id: str,
    request: PermissionRequest,
    reason: str,
) -> ApprovalRequestedEvent:
    return ApprovalRequestedEvent(
        run.session_id,
        run.run_id,
        run.next_sequence(),
        turn_id,
        request_id,
        call.call_id,
        call.name,
        tuple(sorted(request.capabilities)),
        request.risk,
        request.arguments,
        reason,
    )
