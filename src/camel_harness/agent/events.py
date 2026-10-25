from __future__ import annotations

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from camel_harness.llm import AssistantMessage, LlmStreamEvent, Message, ToolResultMessage

from .state import StopReason
from .tools.types import ToolResult

MessageKind: TypeAlias = Literal["user", "assistant", "toolResult"]
AgentEndStatus: TypeAlias = Literal["completed", "failed", "cancelled"]
AgentEventListener = Callable[["AgentEvent"], Awaitable[None] | None]
Unsubscribe = Callable[[], None]
_LISTENER_TIMEOUT_SECONDS = 5
logger = logging.getLogger(__name__)


class _EventDispatcher:
    """顺序交付生命周期事件并隔离故障观察者，不创建后台任务或消息队列。"""

    def __init__(self) -> None:
        self._listeners_by_id: dict[int, AgentEventListener] = {}
        self._next_id = 0

    def subscribe(self, listener: AgentEventListener) -> Unsubscribe:
        listener_id = self._next_id
        self._next_id += 1
        self._listeners_by_id[listener_id] = listener

        def unsubscribe() -> None:
            self._listeners_by_id.pop(listener_id, None)

        return unsubscribe

    def clear(self) -> None:
        self._listeners_by_id.clear()

    async def emit(self, agent_event: AgentEvent) -> None:
        """有界背压不阻止真实 Run 取消；监听器自身取消只移除该监听器。"""

        # 迭代 tuple 快照：emit 期间的订阅/退订只对后续事件生效。
        for listener_id, listener in tuple(self._listeners_by_id.items()):
            try:
                async with asyncio.timeout(_LISTENER_TIMEOUT_SECONDS):
                    result = listener(agent_event)
                    if inspect.isawaitable(result):
                        await result
            except asyncio.CancelledError:
                task = asyncio.current_task()
                # task.cancelling() 区分 Run 被取消（必须继续抛）与监听器自己取消（只移除它）。
                if task is not None and task.cancelling():
                    raise
                self._listeners_by_id.pop(listener_id, None)
            except Exception as exc:
                # 单监听器超过 5s 或抛异常即永久移除，避免慢消费者反复阻塞真实 Run。
                self._listeners_by_id.pop(listener_id, None)
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
    """开始处理工具调用，包含审批、拒绝与实际执行。"""

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
    execution_context: dict[str, str] = field(default_factory=dict)
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
