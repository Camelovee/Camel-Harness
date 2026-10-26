from __future__ import annotations

from dataclasses import dataclass

from camel_harness.llm import (
    AssistantMessage,
    LlmStopReason,
    Message,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)


@dataclass(frozen=True, slots=True)
class ResumePlan:
    """从完整规范消息尾部继续执行所需的最小计划。"""

    resumable: bool
    reason: str
    missing_tool_results: tuple[ToolResultMessage, ...] = ()


def analyze_resume(messages: tuple[Message, ...]) -> ResumePlan:
    """判断会话尾部能否继续，并闭合未完成的工具调用链。"""

    if not messages:
        return ResumePlan(False, "空会话无法恢复")

    last = messages[-1]
    if isinstance(last, UserMessage):
        return ResumePlan(True, "会话停在用户消息之后")

    # 判定顺序即优先级：先看用户消息，再找最后一条 assistant，最后才看 stop_reason。
    assistant_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], AssistantMessage)
        ),
        None,
    )
    if assistant_index is None:
        return ResumePlan(False, "会话中没有可衔接的 assistant 消息")

    assistant = messages[assistant_index]
    assert isinstance(assistant, AssistantMessage)
    calls = tuple(block for block in assistant.content if isinstance(block, ToolUseBlock))
    if calls:
        completed = {
            message.call_id
            for message in messages[assistant_index + 1 :]
            if isinstance(message, ToolResultMessage)
        }
        # 补出的错误结果只表示副作用状态不确定；恢复后应先核验环境，不自动重放。
        missing = tuple(
            ToolResultMessage(
                call_id=call.call_id,
                name=call.name,
                content="工具执行被中断；副作用状态可能不确定，请检查环境后再决定下一步。",
                is_error=True,
            )
            for call in calls
            if call.call_id not in completed
        )
        return ResumePlan(True, "会话停在工具调用链中", missing)

    if isinstance(last, ToolResultMessage):
        return ResumePlan(True, "会话停在工具结果之后")
    if assistant.stop_reason is LlmStopReason.STOP:
        return ResumePlan(False, "会话已经正常结束，请提交新的用户消息")
    return ResumePlan(False, "会话尾部不满足恢复条件")
