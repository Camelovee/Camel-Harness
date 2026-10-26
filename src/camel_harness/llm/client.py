from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .message import (
    AssistantContent,
    AssistantMessage,
    LlmStopReason,
    LlmUsage,
    ReasoningBlock,
    TextBlock,
    ToolUseBlock,
)
from .protocol import (
    LlmAdapter,
    LlmErrorCode,
    LlmRequest,
    LlmStreamEvent,
    MessageCompleted,
    ReasoningDelta,
    StreamFailed,
    StreamStarted,
    TextDelta,
    ToolCallCompleted,
    UsageUpdated,
)


class AssistantMessageAccumulator:
    """组装单次规范响应，不感知运行坐标、事件订阅者或持久化。"""

    def __init__(self, provider: str, model: str) -> None:
        self._provider = provider
        self._model = model
        self._text_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._tool_calls: list[ToolUseBlock] = []
        self._stop_reason: LlmStopReason | None = None
        self._response_model: str | None = None
        self._error: str | None = None
        self.usage: LlmUsage | None = None

    def feed(self, llm_event: LlmStreamEvent) -> None:
        """累积规范内容和累计用量；错误保留到完整性检查，允许调用方先转发事件。"""

        if isinstance(llm_event, StreamStarted | MessageCompleted) and llm_event.model:
            self._response_model = llm_event.model
        if isinstance(llm_event, TextDelta):
            self._text_parts.append(llm_event.text)
        elif isinstance(llm_event, ReasoningDelta):
            self._reasoning_parts.append(llm_event.text)
        elif isinstance(llm_event, ToolCallCompleted):
            self._tool_calls.append(llm_event.tool_call)
        elif isinstance(llm_event, UsageUpdated):
            self.usage = (self.usage or LlmUsage()).merge(llm_event.usage)
        elif isinstance(llm_event, MessageCompleted):
            self._stop_reason = llm_event.stop_reason
        elif isinstance(llm_event, StreamFailed):
            self._error = llm_event.error

    def finish(self) -> AssistantMessage:
        """只返回具有完成事件且结束原因与内容一致的响应。"""

        if self._error is not None:
            raise RuntimeError(self._error)
        # 三条完整性断言：必须有完成事件，且 stop_reason 与内容形态一致。
        if self._stop_reason is None:
            raise RuntimeError("LLM 流缺少 MessageCompleted，禁止使用不完整消息")
        if self._stop_reason is LlmStopReason.TOOL_USE and not self._tool_calls:
            raise RuntimeError("LLM 以 tool_use 结束，但未返回完整工具调用")
        if self._stop_reason is LlmStopReason.STOP and self._tool_calls:
            raise RuntimeError("LLM 以 stop 结束，但响应中存在工具调用")
        blocks: list[AssistantContent] = []
        if self._reasoning_parts:
            blocks.append(ReasoningBlock("".join(self._reasoning_parts)))
        if self._text_parts:
            blocks.append(TextBlock("".join(self._text_parts)))
        blocks.extend(self._tool_calls)
        return AssistantMessage(
            tuple(blocks),
            self._stop_reason,
            provider=self._provider,
            model=self._model,
            # 只在服务端实际模型与请求不同时才记录，避免重复字段。
            response_model=self._response_model if self._response_model != self._model else None,
            usage=self.usage,
        )


class LlmClient:
    """按请求 Provider 路由到无状态适配器，仅在首个事件前按策略重试瞬时失败。"""

    def __init__(self, adapters: LlmAdapter | dict[str, LlmAdapter], max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("LLM 最大尝试次数必须大于 0")
        if isinstance(adapters, LlmAdapter):
            adapters = {adapters.provider_id: adapters}
        if not adapters:
            raise ValueError("至少需要一个 Provider 适配器")
        self._adapters_by_provider = dict(adapters)
        self._max_attempts = max_attempts

    def supports(self, provider: str) -> bool:
        """返回是否配置了指定 Provider 的适配器。"""

        return provider in self._adapters_by_provider

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """执行规范请求；一旦发出事件便不再重试，避免消费者收到重复增量。"""

        adapter = self._adapters_by_provider.get(llm_request.model.provider)
        if adapter is None:
            yield StreamFailed(f"未配置的 Provider：{llm_request.model.provider}", retryable=False)
            return
        for attempt in range(1, self._max_attempts + 1):
            # emitted 一旦为 True，消费者已经看到增量，本轮就不能再重放。
            emitted = False
            failure: StreamFailed | None = None
            stream = adapter.stream(llm_request)
            try:
                async for llm_event in stream:
                    if isinstance(llm_event, StreamFailed):
                        failure = llm_event
                        break
                    emitted = True
                    yield llm_event
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = StreamFailed(str(exc), isinstance(exc, ConnectionError | TimeoutError))
            finally:
                # 失败或取消也要关闭适配器流，避免连接泄漏。
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
            if failure is None:
                return
            if (
                # 已发出增量、溢出、不可重试或次数用尽：直接返回失败，不再重放。
                emitted
                or failure.code is LlmErrorCode.CONTEXT_OVERFLOW
                or not failure.retryable
                or attempt >= self._max_attempts
            ):
                yield failure
                return
            # 指数退避 0.25s、0.5s…；只在首个事件前允许重试。
            await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
