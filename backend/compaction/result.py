from dataclasses import dataclass
from enum import StrEnum

from backend.types import Message


class CompactionTrigger(StrEnum):
    """触发压缩评估的原因。"""

    PRESSURE = "pressure"
    CONTEXT_OVERFLOW = "context_overflow"


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """一次 SessionLog 上下文替换的不可变结果。"""

    start_seq: int
    end_seq: int
    input_tokens: int
    output_tokens: int
    summary_message: Message
