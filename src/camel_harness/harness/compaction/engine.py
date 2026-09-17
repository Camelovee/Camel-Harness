from __future__ import annotations

import json

from camel_harness.harness.session import SessionLog
from camel_harness.llm import LlmClient, LlmRequest, LlmStreamEventType
from camel_harness.options import AgentOptions
from camel_harness.types import Message, MessageRole, StepId, message_to_dict

from .result import CompactionResult, CompactionTrigger

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
        session: SessionLog,
        turn_id: str,
        options: AgentOptions,
        *,
        trigger: CompactionTrigger = CompactionTrigger.PRESSURE,
    ) -> CompactionResult | None:
        """达到压力后总结较早消息，并返回追加式 Replace 操作。"""

        surface = session.surface()
        if len(surface) < self._min_messages_to_compact:
            return None
        messages = tuple(node.message for node in surface)
        estimated_chars = sum(_message_chars(message) for message in messages)
        if (
            trigger is not CompactionTrigger.CONTEXT_OVERFLOW
            and estimated_chars < self._pressure_threshold_chars
        ):
            return None
        return await self._compact(session, turn_id, options)

    async def _compact(
        self,
        session: SessionLog,
        turn_id: str,
        options: AgentOptions,
    ) -> CompactionResult | None:
        """保留最近约四分之一，并把更早的安全区间总结为一条消息。"""

        surface = session.surface()
        if len(surface) < 2:
            return None

        keep_recent = max(1, len(surface) // 4)
        compact_end = len(surface) - keep_recent
        # ToolResult 必须与产生它的 assistant tool_use 一起进入摘要或一起保留。
        while compact_end < len(surface) and surface[compact_end].message.role is MessageRole.TOOL:
            compact_end += 1
        if compact_end < 2 or compact_end >= len(surface):
            return None

        selected = surface[:compact_end]
        replaced = tuple(node.message for node in selected)
        summary, input_tokens, output_tokens = await self._summarize(
            session.session_id, turn_id, options, replaced
        )
        if not summary.strip():
            return None
        summary_message = Message.text(
            MessageRole.USER,
            f"[Compacted History]\n\n{summary.strip()}",
        )
        return CompactionResult(
            start_seq=min(node.seq for node in selected),
            end_seq=max(node.seq for node in selected),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            summary_message=summary_message,
        )

    async def _summarize(
        self,
        session_id: str,
        turn_id: str,
        options: AgentOptions,
        messages: tuple[Message, ...],
    ) -> tuple[str, int, int]:
        """通过统一 LLM Client 生成摘要，不把厂商协议泄漏到 Agent。"""

        transcript = "\n".join(
            json.dumps(_model_message_dict(message), ensure_ascii=False, separators=(",", ":"))
            for message in messages
        )
        request = LlmRequest(
            session_id=session_id,
            turn_id=turn_id,
            step_id=f"compact-{StepId.new().value}",
            provider=options.provider,
            model=options.model,
            messages=(
                Message.text(MessageRole.SYSTEM, _SUMMARY_SYSTEM_PROMPT),
                Message.text(MessageRole.USER, transcript),
            ),
            max_output_tokens=min(options.max_output_tokens, self._max_summary_tokens),
        )
        parts: list[str] = []
        completed = False
        input_tokens = 0
        output_tokens = 0
        async for event in self._llm.stream(request):
            if event.type is LlmStreamEventType.TEXT_DELTA:
                parts.append(str(event.data.get("text", "")))
            elif event.type is LlmStreamEventType.MESSAGE_COMPLETED:
                completed = True
            elif event.type is LlmStreamEventType.USAGE_UPDATED:
                input_tokens = max(input_tokens, int(event.data.get("input_tokens", 0)))
                output_tokens = max(output_tokens, int(event.data.get("output_tokens", 0)))
            elif event.type is LlmStreamEventType.STREAM_FAILED:
                detail = event.data.get("error", event.data.get("message", "上下文压缩请求失败"))
                raise RuntimeError(str(detail))
        if not completed:
            raise RuntimeError("上下文压缩流缺少 MessageCompleted")
        return "".join(parts), input_tokens, output_tokens


def _message_chars(message: Message) -> int:
    """按模型实际可见内容估算占用，不重新注入 Provider reasoning。"""

    return len(json.dumps(_model_message_dict(message), ensure_ascii=False, separators=(",", ":")))


def _model_message_dict(message: Message) -> dict[str, object]:
    """投影用于摘要模型的消息，过滤只供 UI 回放的 reasoning 块。"""

    payload = message_to_dict(message)
    content = payload.get("content", [])
    assert isinstance(content, list)
    payload["content"] = [item for item in content if item.get("type") != "reasoning"]
    return payload
