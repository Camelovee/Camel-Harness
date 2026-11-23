from camel_harness.agent.events import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    SubagentEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
)
from camel_harness.agent.run_ledger import RunLedger
from camel_harness.agent.tools.types import ToolExecutionStatus, ToolResult
from camel_harness.llm import AssistantMessage, LlmStopReason, LlmUsage, TextBlock


def _start(call_id: str, name: str, arguments: dict[str, object]) -> ToolExecutionStartEvent:
    return ToolExecutionStartEvent(
        "session", "run", 1, "turn", call_id=call_id, name=name, arguments=arguments
    )


def _end(call_id: str, name: str, status: ToolExecutionStatus) -> ToolExecutionEndEvent:
    result = ToolResult(call_id, name, "结果").with_execution_status(status)
    return ToolExecutionEndEvent("session", "run", 1, "turn", result=result)


def test_summary_collects_files_approvals_subagents_and_failures() -> None:
    ledger = RunLedger("run-1", "anthropic", "claude-test")
    ledger.observe(_start("write-1", "write_file", {"path": "/workspace/app.py"}))
    ledger.observe(_start("read-1", "read_file", {"path": "/workspace/read.py"}))
    ledger.observe(_start("shell-1", "run_shell", {"command": "false"}))
    ledger.observe(
        ApprovalRequestedEvent(
            "session",
            "run",
            1,
            "turn",
            request_id="approval-1",
            call_id="write-1",
            name="write_file",
            capabilities=("write",),
            risk="write",
            arguments={},
            reason="写入",
        )
    )
    ledger.observe(
        ApprovalResolvedEvent(
            "session",
            "run",
            1,
            "turn",
            request_id="approval-1",
            call_id="write-1",
            approved=True,
            reason="允许",
        )
    )
    ledger.observe(_end("write-1", "write_file", ToolExecutionStatus.SUCCEEDED))
    ledger.observe(_end("read-1", "read_file", ToolExecutionStatus.SUCCEEDED))
    ledger.observe(_end("shell-1", "run_shell", ToolExecutionStatus.FAILED))
    ledger.observe(
        SubagentEvent(
            "session",
            "run",
            1,
            "turn",
            call_id="delegate-1",
            agent_type="explore",
            event={"session_id": "sub-1", "type": "agent_start"},
        )
    )
    ledger.observe(
        TurnEndEvent(
            "session",
            "run",
            1,
            "turn",
            message=AssistantMessage(
                (TextBlock("回答"),), LlmStopReason.STOP, usage=LlmUsage(30, 12)
            ),
            tool_results=(),
        )
    )

    summary = ledger.finish(status="completed", stop_reason="model_completed", total_tokens=42)

    assert summary.files == ("/workspace/app.py",)
    assert summary.tool_failures == 1
    assert summary.subagent_ids == ("sub-1",)
    assert summary.input_tokens == 30
    assert summary.output_tokens == 12
    assert summary.total_tokens == 42
    assert summary.approvals[0].tool_name == "write_file"
    assert summary.approvals[0].approved is True


def test_rejected_approval_is_recorded_but_not_counted_as_tool_failure() -> None:
    ledger = RunLedger("run-2", "openai", "gpt-test")
    ledger.observe(
        ApprovalRequestedEvent(
            "session",
            "run",
            1,
            "turn",
            request_id="approval-2",
            call_id="shell-2",
            name="run_shell",
            capabilities=("execute",),
            risk="execute",
            arguments={},
            reason="执行",
        )
    )
    ledger.observe(
        ApprovalResolvedEvent(
            "session",
            "run",
            1,
            "turn",
            request_id="approval-2",
            call_id="shell-2",
            approved=False,
            reason="拒绝",
        )
    )
    ledger.observe(_end("shell-2", "run_shell", ToolExecutionStatus.NOT_EXECUTED))

    summary = ledger.finish(status="failed", stop_reason="tool_failed", total_tokens=0)

    assert summary.approvals[0].approved is False
    assert summary.tool_failures == 0
    assert summary.input_tokens is None
    assert summary.output_tokens is None
