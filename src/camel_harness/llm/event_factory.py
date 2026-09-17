from typing import Any

from .request import LlmRequest
from .stream_event import LlmStreamEvent, LlmStreamEventType


def provider_event(
    provider: str,
    request: LlmRequest,
    sequence: int,
    event_type: LlmStreamEventType,
    data: dict[str, Any],
) -> LlmStreamEvent:
    """补齐厂商事件共享的运行标识，避免协议适配器重复构造事件信封。"""

    return LlmStreamEvent(
        type=event_type,
        session_id=request.session_id,
        turn_id=request.turn_id,
        step_id=request.step_id,
        sequence=sequence,
        provider=provider,
        data=data,
    )
