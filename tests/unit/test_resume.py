from camel_harness.agent.session import analyze_resume
from camel_harness.llm import (
    AssistantMessage,
    LlmStopReason,
    ToolResultMessage,
    ToolUseBlock,
)


def test_resume_plan_closes_only_missing_tool_results() -> None:
    assistant = AssistantMessage(
        (
            ToolUseBlock("call-1", "read_file", {"path": "a.txt"}),
            ToolUseBlock("call-2", "run_shell", {"command": "echo ok"}),
        ),
        LlmStopReason.TOOL_USE,
    )
    completed = ToolResultMessage("call-1", "read_file", "内容")

    plan = analyze_resume((assistant, completed))

    assert plan.resumable
    assert [message.call_id for message in plan.missing_tool_results] == ["call-2"]
    assert plan.missing_tool_results[0].is_error
