from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class SessionId:
    """会话标识，使用值对象避免不同类型的 ID 被误传。"""

    value: str

    @classmethod
    def new(cls) -> SessionId:
        return cls(uuid4().hex)


@dataclass(frozen=True, slots=True)
class TurnId:
    """一次用户输入及其后续工具循环的标识。"""

    value: str

    @classmethod
    def new(cls) -> TurnId:
        return cls(uuid4().hex)


@dataclass(frozen=True, slots=True)
class StepId:
    """单次模型请求的标识。"""

    value: str

    @classmethod
    def new(cls) -> StepId:
        return cls(uuid4().hex)


@dataclass(frozen=True, slots=True)
class AgentOptions:
    """创建和运行 ReAct Agent 所需的稳定选项。"""

    model: str
    max_output_tokens: int = 4096
    max_steps: int = 20
    max_total_tokens: int = 200_000
    max_duration_seconds: float = 900
    thinking: bool = False

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Agent Model 不能为空")
        if self.max_output_tokens <= 0:
            raise ValueError("Agent 最大输出 token 必须大于 0")
        if self.max_steps <= 0:
            raise ValueError("Agent 最大 Step 数必须大于 0")
        if self.max_total_tokens <= 0:
            raise ValueError("Agent 总 token 预算必须大于 0")
        if self.max_duration_seconds <= 0:
            raise ValueError("Agent 最大运行时间必须大于 0")


class RunStatus(StrEnum):
    """Agent 一次运行的状态。"""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StopReason(StrEnum):
    """Agent Loop 停止的规范原因。"""

    MODEL_COMPLETED = "model_completed"
    MODEL_LENGTH = "model_length"
    MAX_STEPS = "max_steps"
    PROVIDER_FAILED = "provider_failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(slots=True)
class AgentRun:
    """Agent 运行状态，维护 Step 计数和终态不变量。"""

    session_id: str
    turn_id: str
    max_steps: int = 20
    status: RunStatus = RunStatus.IDLE
    step: int = 0
    stop_reason: StopReason | None = None
    max_total_tokens: int = 200_000
    total_tokens: int = 0

    def start(self) -> None:
        if self.status is not RunStatus.IDLE:
            raise RuntimeError("只有空闲运行可以启动")
        self.status = RunStatus.RUNNING

    def next_step(self) -> int:
        if self.status is not RunStatus.RUNNING:
            raise RuntimeError("只有运行中的 Agent 可以进入下一 Step")
        if self.step >= self.max_steps:
            self.fail(StopReason.MAX_STEPS)
            raise RuntimeError("Agent 已达到最大 Step 数")
        self.step += 1
        return self.step

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
        """累计 Step 用量并强制执行总 token 预算。"""

        self.total_tokens += input_tokens + output_tokens
        if self.total_tokens > self.max_total_tokens:
            self.fail(StopReason.BUDGET_EXCEEDED)
            raise RuntimeError("Agent 已超过本轮 token 预算")
