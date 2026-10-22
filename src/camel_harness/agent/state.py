from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from camel_harness.llm import UserMessage


@dataclass(frozen=True, slots=True)
class AgentOptions:
    """创建 Agent 时的稳定选项；模型与 per-run 输入由 run() 参数承担。"""

    max_duration_seconds: float = 900
    max_steering_messages: int = 32
    max_steering_chars: int = 100_000

    def __post_init__(self) -> None:
        if self.max_duration_seconds <= 0:
            raise ValueError("Agent 最大运行时间必须大于 0")
        if self.max_steering_messages <= 0:
            raise ValueError("Agent 运行中输入队列上限必须大于 0")
        if self.max_steering_chars <= 0:
            raise ValueError("Agent 运行中输入长度上限必须大于 0")


class RunStatus(StrEnum):
    """Agent 一次运行的状态。"""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StopReason(StrEnum):
    """Agent Run 停止的规范原因。"""

    MODEL_COMPLETED = "model_completed"
    MODEL_LENGTH = "model_length"
    MAX_TURNS = "max_turns"
    PROVIDER_FAILED = "provider_failed"
    SESSION_FAILED = "session_failed"
    CONTEXT_FAILED = "context_failed"
    RESOURCE_FAILED = "resource_failed"
    TOOL_FAILED = "tool_failed"
    INTERNAL_ERROR = "internal_error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass(slots=True)
class AgentRun:
    """Agent 运行状态，维护 Turn、事件序号和终态不变量。"""

    session_id: str
    run_id: str
    max_turns: int = 20
    status: RunStatus = RunStatus.IDLE
    turn: int = 0
    sequence: int = 0
    stop_reason: StopReason | None = None
    total_tokens: int = 0
    context_overflow_recovery_used: bool = False
    consecutive_length_recoveries: int = 0
    last_output_budget: int = 0
    last_output_tokens: int | None = None
    last_reasoning_tokens: int | None = None

    def length_error(self, *, has_tool_calls: bool) -> str:
        """保留本次预算和服务端用量，未知值不伪装成零。"""
        kind = "工具参数被截断，未执行" if has_tool_calls else "纯文本内容未完整生成"
        output_tokens = self.last_output_tokens if self.last_output_tokens is not None else "未知"
        reasoning_tokens = (
            self.last_reasoning_tokens if self.last_reasoning_tokens is not None else "未知"
        )
        return (
            f"模型输出达到长度上限：{kind}；请求预算={self.last_output_budget}，"
            f"输出用量={output_tokens}，思考用量={reasoning_tokens}，"
            f"连续恢复次数={self.consecutive_length_recoveries}。"
            "请检查模型输出预算或拆分修改任务"
        )

    def start(self) -> None:
        if self.status is not RunStatus.IDLE:
            raise RuntimeError("只有空闲运行可以启动")
        self.status = RunStatus.RUNNING

    def next_turn(self) -> int:
        if self.status is not RunStatus.RUNNING:
            raise RuntimeError("只有运行中的 Agent 可以进入下一 Turn")
        if self.turn >= self.max_turns:
            self.fail(StopReason.MAX_TURNS)
            raise RuntimeError("Agent 已达到最大 Turn 数")
        self.turn += 1
        return self.turn

    def next_sequence(self) -> int:
        """返回当前 Run 内全局单调递增的事件序号。"""

        self.sequence += 1
        return self.sequence

    def complete(self) -> None:
        self.status = RunStatus.COMPLETED
        self.stop_reason = StopReason.MODEL_COMPLETED

    def fail(self, reason: StopReason) -> None:
        self.status = RunStatus.FAILED
        self.stop_reason = reason

    def cancel(self) -> None:
        self.status = RunStatus.CANCELLED
        self.stop_reason = StopReason.CANCELLED

    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """累计已知请求用量，仅作审计，不代表上下文窗口大小。"""

        self.total_tokens += input_tokens + output_tokens


@dataclass(frozen=True, slots=True)
class QueuedInput:
    """尚未到达安全消费点的运行中用户输入。"""

    input_id: str
    message: UserMessage


@dataclass(frozen=True, slots=True)
class SteerResult:
    """一次运行中输入成功入队后的公开确认。"""

    input_id: str
    run_id: str


@dataclass(slots=True)
class RunControl:
    """一个 Agent 当前唯一 Run 的瞬时控制状态。"""

    agent_run: AgentRun
    done: asyncio.Event
    owner_task: asyncio.Task[object] | None = None
    steering: deque[QueuedInput] = field(default_factory=deque)
    accepting_input: bool = True
    abort_requested: bool = False
