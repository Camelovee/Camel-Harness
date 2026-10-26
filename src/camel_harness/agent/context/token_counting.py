"""统一模型可见内容的估算与基于响应用量的上下文读数。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from camel_harness.llm import (
    AssistantMessage,
    LlmModel,
    Message,
    TextBlock,
    ToolDefinition,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)

from ..session.events import ContextSnapshot


@dataclass(frozen=True, slots=True)
class ContextTokenEstimate:
    """usage 是最近响应的基数；尾部估算不重复包含该次响应。"""

    total_tokens: int
    usage_tokens: int
    trailing_tokens: int
    last_usage_index: int | None


def estimate_text_tokens(text: str) -> int:
    """按 UTF-8 字节数除以 3 向上取整；不是厂商精确 tokenizer。"""

    # 英文约 4 字符/token、中文约 1 字符/token；字节除以 3 是跨语言折中，英文偏保守。
    return (len(text.encode("utf-8")) + 2) // 3


def estimate_message_tokens(message: Message) -> int:
    """只估算模型可见投影，排除展示与审计数据。"""

    return estimate_text_tokens(json.dumps(model_message_dict(message), ensure_ascii=False))


def estimate_context_overhead_tokens(
    system_prompt: str, tool_definitions: tuple[ToolDefinition, ...] = ()
) -> int:
    """估算完整系统提示与工具定义，供全文及压缩后预算检查使用。"""

    tool_text = json.dumps(
        [
            {"name": tool.name, "description": tool.description, "schema": tool.input_schema}
            for tool in tool_definitions
        ],
        ensure_ascii=False,
    )
    return estimate_text_tokens(system_prompt + tool_text)


def estimate_context_tokens(
    snapshot: ContextSnapshot,
    model: LlmModel,
    *,
    context_overhead_tokens: int = 0,
) -> ContextTokenEstimate:
    """优先使用有效 usage 加尾部消息；没有时估算完整上下文。

    usage 已包含当时的系统提示、工具和该次输出，不能再次添加这些开销。
    不校验动态资源变化；压缩前的 usage 通过快照边界排除。
    """

    messages = snapshot.messages
    # 从压缩边界之后回扫最近的有效 usage；边界之前的统计属于旧投影，不能作为基数。
    for index in range(len(messages) - 1, snapshot.usage_start_index - 1, -1):
        usage_tokens = _matching_usage_tokens(messages[index], model)
        if usage_tokens is not None:
            trailing_tokens = sum(
                estimate_message_tokens(message) for message in messages[index + 1 :]
            )
            return ContextTokenEstimate(
                usage_tokens + trailing_tokens, usage_tokens, trailing_tokens, index
            )
    estimated_tokens = context_overhead_tokens + sum(
        estimate_message_tokens(message) for message in messages
    )
    return ContextTokenEstimate(estimated_tokens, 0, estimated_tokens, None)


def _matching_usage_tokens(message: Message, model: LlmModel) -> int | None:
    """未知、不完整、全零或不同模型的统计不能充当当前请求基数。"""

    if (
        not isinstance(message, AssistantMessage)
        or message.provider != model.provider
        or message.model != model.model
        or message.usage is None
    ):
        return None
    total_tokens = message.usage.total_tokens
    return total_tokens if total_tokens is not None and total_tokens > 0 else None


def model_message_dict(message: Message) -> dict[str, object]:
    """投影用于摘要模型的消息，过滤只供 UI 回放的 reasoning 块。"""

    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, ToolResultMessage):
        return {
            "role": "toolResult",
            "call_id": message.call_id,
            "name": message.name,
            "content": message.content,
            "is_error": message.is_error,
        }
    content: list[dict[str, object]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            content.append(
                {
                    "type": "tool_use",
                    "call_id": block.call_id,
                    "name": block.name,
                    "arguments": block.arguments,
                }
            )
    return {"role": "assistant", "content": content, "stop_reason": message.stop_reason.value}
