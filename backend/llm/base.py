from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from .request import LlmRequest
from .stream_event import LlmStreamEvent


class LlmAdapter(ABC):
    """厂商无关的 LLM 流式协议，具体实现与官方 SDK 位于同一功能模块。"""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """返回当前适配器服务的 Provider 标识。"""

        raise NotImplementedError

    @abstractmethod
    def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """把规范请求转换为厂商调用，并返回规范流式事件。"""

        raise NotImplementedError
