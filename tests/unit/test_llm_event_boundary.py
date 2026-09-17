from camel_harness.llm import LlmStreamEventType


def test_llm_event_types_do_not_contain_agent_runtime_events() -> None:
    """审批和工具结果属于 Agent Runtime，不得重新进入 LLM 协议。"""

    values = {event_type.value for event_type in LlmStreamEventType}
    assert "approval_requested" not in values
    assert "tool_result" not in values
