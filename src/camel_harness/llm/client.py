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
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._calls: list[ToolUseBlock] = []
        self._stop_reason: LlmStopReason | None = None
        self._response_model: str | None = None
        self._error: str | None = None
        self.usage: LlmUsage | None = None

    def feed(self, event: LlmStreamEvent) -> None:
        """累积规范内容和累计用量；错误保留到完整性检查，允许调用方先转发事件。"""

        if isinstance(event, StreamStarted | MessageCompleted) and event.model:
            self._response_model = event.model
        if isinstance(event, TextDelta):
            self._text.append(event.text)
        elif isinstance(event, ReasoningDelta):
            self._reasoning.append(event.text)
        elif isinstance(event, ToolCallCompleted):
            self._calls.append(event.tool_call)
        elif isinstance(event, UsageUpdated):
            self.usage = (self.usage or LlmUsage()).merge(event.usage)
        elif isinstance(event, MessageCompleted):
            self._stop_reason = event.stop_reason
        elif isinstance(event, StreamFailed):
            self._error = event.error

    def finish(self) -> AssistantMessage:
        """只返回具有完成事件且结束原因与内容一致的响应。"""

        if self._error is not None:
            raise RuntimeError(self._error)
        if self._stop_reason is None:
            raise RuntimeError("LLM 流缺少 MessageCompleted，禁止使用不完整消息")
        if self._stop_reason is LlmStopReason.TOOL_USE and not self._calls:
            raise RuntimeError("LLM 以 tool_use 结束，但未返回完整工具调用")
        if self._stop_reason is LlmStopReason.STOP and self._calls:
            raise RuntimeError("LLM 以 stop 结束，但响应中存在工具调用")
        blocks: list[AssistantContent] = []
        if self._reasoning:
            blocks.append(ReasoningBlock("".join(self._reasoning)))
        if self._text:
            blocks.append(TextBlock("".join(self._text)))
        blocks.extend(self._calls)
        return AssistantMessage(
            tuple(blocks),
            self._stop_reason,
            provider=self._provider,
            model=self._model,
            response_model=self._response_model if self._response_model != self._model else None,
            usage=self.usage,
        )


class LlmClient:
    """LLM 公开门面，仅在首个事件前按策略重试瞬时失败。"""

    def __init__(self, adapter: LlmAdapter, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("LLM 最大尝试次数必须大于 0")
        self._adapter = adapter
        self._max_attempts = max_attempts

    @property
    def provider_id(self) -> str:
        """返回当前 Client 绑定的 Provider。"""

        return self._adapter.provider_id

    async def close(self) -> None:
        """只有组合层拥有 Client；子 Agent 借用时不得关闭共享连接。"""
        await self._adapter.close()

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """执行规范请求；一旦发出事件便不再重试，避免消费者收到重复增量。"""

        for attempt in range(1, self._max_attempts + 1):
            emitted = False
            failure: StreamFailed | None = None
            stream = self._adapter.stream(request)
            try:
                async for event in stream:
                    if isinstance(event, StreamFailed):
                        failure = event
                        break
                    emitted = True
                    yield event
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = StreamFailed(str(exc), isinstance(exc, ConnectionError | TimeoutError))
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
            if failure is None:
                return
            if emitted or not failure.retryable or attempt >= self._max_attempts:
                yield failure
                return
            await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
