"""从公开事件累积一次 Run 的审计摘要，不读取会话或工具内部状态。"""

from __future__ import annotations

from datetime import UTC, datetime

from .events import (
    AgentEvent,
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    SubagentEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
)
from .session.types import ApprovalDecision, RunSummary
from .tools.types import ToolExecutionStatus

_WRITE_TOOLS = frozenset({"write_file", "edit_file"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RunLedger:
    """只观察事件，不改变运行结果；摘要写入失败由调用方决定如何处理。"""

    def __init__(self, run_id: str, provider: str, model: str) -> None:
        self._run_id = run_id
        self._provider = provider
        self._model = model
        self._started_at = _now()
        self._call_names: dict[str, str] = {}
        self._call_paths: dict[str, str] = {}
        self._approval_names: dict[str, str] = {}
        self._approvals: list[ApprovalDecision] = []
        self._files: list[str] = []
        self._subagent_ids: list[str] = []
        self._tool_failures = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._saw_usage = False

    def observe(self, agent_event: AgentEvent) -> None:
        if isinstance(agent_event, ToolExecutionStartEvent):
            self._call_names[agent_event.call_id] = agent_event.name
            path = agent_event.arguments.get("path")
            if agent_event.name in _WRITE_TOOLS and isinstance(path, str):
                self._call_paths[agent_event.call_id] = path
        elif isinstance(agent_event, ApprovalRequestedEvent):
            self._approval_names[agent_event.request_id] = agent_event.name
        elif isinstance(agent_event, ApprovalResolvedEvent):
            self._approvals.append(
                ApprovalDecision(
                    request_id=agent_event.request_id,
                    tool_name=self._approval_names.get(
                        agent_event.request_id,
                        self._call_names.get(agent_event.call_id, ""),
                    ),
                    approved=agent_event.approved,
                    decided_at=_now(),
                )
            )
        elif isinstance(agent_event, ToolExecutionEndEvent):
            self._observe_tool_result(agent_event)
        elif isinstance(agent_event, TurnEndEvent):
            self._observe_usage(agent_event)
        elif isinstance(agent_event, SubagentEvent):
            child_id = agent_event.event.get("session_id")
            if isinstance(child_id, str) and child_id not in self._subagent_ids:
                self._subagent_ids.append(child_id)

    def _observe_tool_result(self, agent_event: ToolExecutionEndEvent) -> None:
        status = agent_event.result.metadata.get("execution_status")
        if status == ToolExecutionStatus.FAILED:
            self._tool_failures += 1
        elif status == ToolExecutionStatus.SUCCEEDED:
            path = self._call_paths.get(agent_event.result.call_id)
            if path is not None and path not in self._files:
                self._files.append(path)

    def _observe_usage(self, agent_event: TurnEndEvent) -> None:
        usage = agent_event.message.usage
        if usage is None:
            return
        if usage.input_tokens is not None:
            self._input_tokens += usage.input_tokens
            self._saw_usage = True
        if usage.output_tokens is not None:
            self._output_tokens += usage.output_tokens
            self._saw_usage = True

    def finish(self, *, status: str, stop_reason: str | None, total_tokens: int) -> RunSummary:
        return RunSummary(
            run_id=self._run_id,
            started_at=self._started_at,
            finished_at=_now(),
            status=status,
            stop_reason=stop_reason,
            provider=self._provider,
            model=self._model,
            total_tokens=total_tokens,
            input_tokens=self._input_tokens if self._saw_usage else None,
            output_tokens=self._output_tokens if self._saw_usage else None,
            files=tuple(self._files),
            approvals=tuple(self._approvals),
            subagent_ids=tuple(self._subagent_ids),
            tool_failures=self._tool_failures,
        )
