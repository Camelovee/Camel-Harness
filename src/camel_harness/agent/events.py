from __future__ import annotations

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from camel_harness.llm import AssistantMessage, LlmStreamEvent, Message, ToolResultMessage

from .state import AgentRun, StopReason
from .tools.permissions import PermissionRequest
from .tools.types import ToolCall, ToolResult

MessageKind: TypeAlias = Literal["user", "assistant", "toolResult"]
AgentEndStatus: TypeAlias = Literal["completed", "failed", "cancelled"]
AgentEventListener = Callable[["AgentEvent"], Awaitable[None] | None]
Unsubscribe = Callable[[], None]
_LISTENER_TIMEOUT_SECONDS = 5
logger = logging.getLogger(__name__)


class _EventDispatcher:
    """顺序交付生命周期事件并隔离故障观察者，不创建后台任务或消息队列。"""

    def __init__(self) -> None:
        self._listeners: dict[int, AgentEventListener] = {}
        self._next_id = 0

    def subscribe(self, listener: AgentEventListener) -> Unsubscribe:
        listener_id = self._next_id
        self._next_id += 1
        self._listeners[listener_id] = listener

        def unsubscribe() -> None:
            self._listeners.pop(listener_id, None)

        return unsubscribe

    def clear(self) -> None:
        self._listeners.clear()

    async def emit(self, event: AgentEvent) -> None:
        """有界背压不阻止真实 Run 取消；监听器自身取消只移除该监听器。"""

        for listener_id, listener in tuple(self._listeners.items()):
            try:
                async with asyncio.timeout(_LISTENER_TIMEOUT_SECONDS):
                    result = listener(event)
                    if inspect.isawaitable(result):
                        await result
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                self._listeners.pop(listener_id, None)
            except Exception as exc:
                self._listeners.pop(listener_id, None)
                logger.warning("移除故障事件订阅者：%s", type(exc).__name__)


@dataclass(frozen=True, slots=True)
class AgentEventBase(ABC):
    """共享公开事件的运行坐标；具体事件仍组成判别联合。"""

    session_id: str
    run_id: str
    sequence: int

    @property
    @abstractmethod
    def type(self) -> str:
        """返回具体事件的判别值。"""


@dataclass(frozen=True, slots=True)
class TurnEventBase(AgentEventBase, ABC):
    """共享 Turn 内事件的坐标。"""

    turn_id: str


@dataclass(frozen=True, slots=True)
class MessageEventBase(TurnEventBase, ABC):
    """共享一条消息的标识。"""

    message_id: str


@dataclass(frozen=True, slots=True)
class AgentStartEvent(AgentEventBase):
    """一次 Agent 执行已开始。"""

    type: Literal["agent_start"] = field(default="agent_start", init=False)


@dataclass(frozen=True, slots=True)
class AgentEndEvent(AgentEventBase):
    """一次 Agent 执行已进入唯一终态。"""

    status: AgentEndStatus
    stop_reason: StopReason | None
    total_tokens: int
    error: str | None = None
    type: Literal["agent_end"] = field(default="agent_end", init=False)


@dataclass(frozen=True, slots=True)
class TurnStartEvent(TurnEventBase):
    """一次 assistant 响应及其工具阶段已开始。"""

    turn: int
    type: Literal["turn_start"] = field(default="turn_start", init=False)


@dataclass(frozen=True, slots=True)
class TurnEndEvent(TurnEventBase):
    """一次 assistant 响应及其工具阶段已结束。"""

    message: AssistantMessage
    tool_results: tuple[ToolResultMessage, ...]
    type: Literal["turn_end"] = field(default="turn_end", init=False)


@dataclass(frozen=True, slots=True)
class MessageStartEvent(MessageEventBase):
    """一条用户、assistant 或工具结果消息已开始。"""

    role: MessageKind
    type: Literal["message_start"] = field(default="message_start", init=False)


@dataclass(frozen=True, slots=True)
class MessageUpdateEvent(MessageEventBase):
    """assistant 消息的一个厂商无关增量。"""

    assistant_event: LlmStreamEvent
    type: Literal["message_update"] = field(default="message_update", init=False)


@dataclass(frozen=True, slots=True)
class MessageEndEvent(MessageEventBase):
    """一条规范消息已完整生成并可以持久化。"""

    message: Message
    type: Literal["message_end"] = field(default="message_end", init=False)


@dataclass(frozen=True, slots=True)
class ToolExecutionStartEvent(TurnEventBase):
    """工具调用已进入实际执行阶段。"""

    call_id: str
    name: str
    arguments: dict[str, Any]
    type: Literal["tool_execution_start"] = field(default="tool_execution_start", init=False)


@dataclass(frozen=True, slots=True)
class ToolExecutionEndEvent(TurnEventBase):
    """工具调用已归一化为最终结果。"""

    result: ToolResult
    type: Literal["tool_execution_end"] = field(default="tool_execution_end", init=False)


@dataclass(frozen=True, slots=True)
class ApprovalRequestedEvent(TurnEventBase):
    """工具调用已挂起并等待用户审批。"""

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
