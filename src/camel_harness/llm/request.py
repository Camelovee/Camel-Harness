from __future__ import annotations

from dataclasses import dataclass

from camel_harness.types import Message, ToolDefinition


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """单次模型请求，显式携带运行时路由所需的 Provider 与模型。"""

    session_id: str
    turn_id: str
    step_id: str
    provider: str
    model: str
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()
    max_output_tokens: int = 4096
    temperature: float | None = None
    thinking: bool = False
