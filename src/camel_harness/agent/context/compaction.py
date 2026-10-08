from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from camel_harness.agent.session import ContextSnapshot, message_to_dict
from camel_harness.agent.state import AgentOptions
from camel_harness.llm import (
    AssistantMessage,
    LlmClient,
    LlmRequest,
    LlmStopReason,
    LlmUsage,
    Message,
    MessageCompleted,
    StreamFailed,
    TextDelta,
    ToolResultMessage,
    UsageUpdated,
    UserMessage,
)


class CompactionTrigger(StrEnum):
    """触发压缩评估的原因。"""

    PRESSURE = "pressure"
    CONTEXT_OVERFLOW = "context_overflow"


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """一次 ContextSnapshot 上下文替换的不可变结果。"""

    first_kept_entry_id: str
    tokens_before: int
    input_tokens: int
    output_tokens: int
    summary: str
    usage: LlmUsage | None = None


_SUMMARY_SYSTEM_PROMPT = """你负责压缩 Agent 的历史上下文。
请用中文生成一份紧凑但可继续工作的摘要，必须保留：
1. 用户目标、约束和偏好；
2. 已完成的修改、关键决策及其原因；
3. 重要文件、标识符、命令结果和错误信息；
4. 尚未完成的任务、风险和下一步；
5. 工具调用产生的、后续推理仍需要的事实。
不要添加原对话中不存在的信息，也不要回答原问题。仅输出摘要正文。"""


class CompactionEngine:
    """按“旧 3/4 摘要、近 1/4 保留”策略压缩 Agent 会话上下文。"""

    def __init__(
        self,
        llm: LlmClient,
        pressure_threshold_chars: int = 120_000,
        min_messages_to_compact: int = 8,
        max_summary_tokens: int = 2048,
    ) -> None:
        if pressure_threshold_chars <= 0:
            raise ValueError("上下文字符压力阈值必须大于 0")
        if min_messages_to_compact < 2:
            raise ValueError("最小压缩消息数不能小于 2")
        if max_summary_tokens <= 0:
            raise ValueError("摘要最大 token 数必须大于 0")
        self._llm = llm
        self._pressure_threshold_chars = pressure_threshold_chars
        self._min_messages_to_compact = min_messages_to_compact
        self._max_summary_tokens = max_summary_tokens

    async def compact_if_needed(
        self,
        snapshot: ContextSnapshot,
        options: AgentOptions,
        *,
        trigger: CompactionTrigger = CompactionTrigger.PRESSURE,
    ) -> CompactionResult | None:
        """达到压力后总结较早消息，并返回压缩计划。"""

        surface = snapshot.nodes
        if len(surface) < self._min_messages_to_compact:
            return None
        messages = tuple(node.message for node in surface)
        estimated_chars = sum(_message_chars(message) for message in messages)
        if (
            trigger is not CompactionTrigger.CONTEXT_OVERFLOW
            and estimated_chars < self._pressure_threshold_chars
        ):
            return None
        return await self._compact(snapshot, options)

    async def _compact(
        self,
        snapshot: ContextSnapshot,
        options: AgentOptions,
    ) -> CompactionResult | None:
        """保留最近约四分之一，并把更早的安全区间总结为一条消息。"""

        surface = snapshot.nodes
        if len(surface) < 2:
            return None

        keep_recent = max(1, len(surface) // 4)
        compact_end = len(surface) - keep_recent
        # ToolResult 必须与产生它的 assistant tool_use 一起进入摘要或一起保留。
        while compact_end < len(surface) and isinstance(
            surface[compact_end].message, ToolResultMessage
        ):
            compact_end += 1
        if compact_end < 2 or compact_end >= len(surface):
            return None

        selected = surface[:compact_end]
        replaced = tuple(node.message for node in selected)
        summary, usage = await self._summarize(options, replaced)
        if not summary.strip():
            return None
        return CompactionResult(
            first_kept_entry_id=surface[compact_end].entry_id,
            tokens_before=(sum(_message_chars(node.message) for node in surface) + 3) // 4,
            input_tokens=usage.input_tokens or 0 if usage else 0,
            output_tokens=usage.output_tokens or 0 if usage else 0,
            summary=summary.strip(),
            usage=usage,
        )

    async def _summarize(
        self,
        options: AgentOptions,
        messages: tuple[Message, ...],
    ) -> tuple[str, LlmUsage | None]:
        """通过统一 LLM Client 生成摘要，不把厂商协议泄漏到 Agent。"""

        transcript = "\n".join(
            json.dumps(_model_message_dict(message), ensure_ascii=False, separators=(",", ":"))
            for message in messages
        )
        request = LlmRequest(
            model=options.model,
            messages=(UserMessage(transcript),),
            system_prompt=_SUMMARY_SYSTEM_PROMPT,
            max_output_tokens=min(options.max_output_tokens, self._max_summary_tokens),
        )
        parts: list[str] = []
        completed = False
        completion_reason: LlmStopReason | None = None
        usage: LlmUsage | None = None
        async for event in self._llm.stream(request):
            if isinstance(event, TextDelta):
                parts.append(event.text)
            elif isinstance(event, MessageCompleted):
                completed = True
                completion_reason = event.stop_reason
            elif isinstance(event, UsageUpdated):
                usage = (usage or LlmUsage()).merge(event.usage)
            elif isinstance(event, StreamFailed):
                raise RuntimeError(event.error)
        if not completed:
            raise RuntimeError("上下文压缩流缺少 MessageCompleted")
        if completion_reason is not LlmStopReason.STOP:
            raise RuntimeError(f"上下文压缩未正常完成：{completion_reason}")
        return "".join(parts), usage


def _message_chars(message: Message) -> int:
    """按模型实际可见内容估算占用，不重新注入 Provider reasoning。"""

    return len(json.dumps(_model_message_dict(message), ensure_ascii=False, separators=(",", ":")))


def _model_message_dict(message: Message) -> dict[str, object]:
    """投影用于摘要模型的消息，过滤只供 UI 回放的 reasoning 块。"""

    payload = message_to_dict(message)
    # 模型摘要只看到对话内容，不把审计统计与文件元数据计入上下文预算。
    for key in ("provider", "model", "response_model", "usage", "details"):
        payload.pop(key, None)
    if isinstance(message, AssistantMessage):
        content = payload.get("content", [])
        assert isinstance(content, list)
        payload["content"] = [item for item in content if item.get("type") != "reasoning"]
    return payload
