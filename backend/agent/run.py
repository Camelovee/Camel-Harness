from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
    MAX_STEPS = "max_steps"
    PROVIDER_FAILED = "provider_failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(slots=True)
class AgentRun:
    """Agent 运行聚合，维护 Step 计数和终态不变量。"""

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
