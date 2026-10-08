from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .protocol import LlmAdapter, LlmRequest, LlmStreamEvent


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

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """执行规范请求；一旦发出事件便不再重试，避免消费者收到重复增量。"""

        for attempt in range(1, self._max_attempts + 1):
            emitted = False
            pending_failure: LlmStreamEvent | None = None
            try:
                async for event in self._adapter.stream(request):
                    if not emitted and event.type == "stream_failed":
                        pending_failure = event
                        continue
                    emitted = True
                    yield event
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                if emitted or attempt >= self._max_attempts:
                    if pending_failure is not None:
                        yield pending_failure
                    raise
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
