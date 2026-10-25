from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MemoryTarget = Literal["memory", "user"]
MemoryAction = Literal["add", "replace", "remove"]


class MemoryValidationError(ValueError):
    """内容或参数不合法；操作未执行。"""


class MemoryTargetDisabledError(MemoryValidationError):
    """目标记忆已关闭；操作未执行。"""


class MemoryLimitError(MemoryValidationError):
    """写入后超过字符上限；操作未执行。"""

    def __init__(
        self,
        message: str,
        *,
        target: MemoryTarget,
        used_chars: int,
        candidate_chars: int,
        limit_chars: int,
        entries: tuple[str, ...],
    ) -> None:
        super().__init__(message)
        self.target = target
        self.used_chars = used_chars
        self.candidate_chars = candidate_chars
        self.limit_chars = limit_chars
        self.entries = entries


class MemoryMatchError(MemoryValidationError):
    """old_text 无法唯一定位条目；操作未执行。"""


class MemoryNotFoundError(MemoryMatchError):
    """old_text 没有匹配任何条目。"""


class MemoryAmbiguousMatchError(MemoryMatchError):
    """old_text 匹配多个条目。"""

    def __init__(self, message: str, *, matches: tuple[str, ...]) -> None:
        super().__init__(message)
        self.matches = matches


class MemoryStorageError(RuntimeError):
    """记忆提交故障；替换后确认失败可能已经产生持久副作用。"""


@dataclass(frozen=True, slots=True)
class MemoryDocument:
    """一个目标的当前条目与使用量。"""

    target: MemoryTarget
    entries: tuple[str, ...]
    text: str
    used_chars: int
    limit_chars: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class MemoryMutation:
    """一次记忆变更的结果；重复添加与无变化替换的 changed 为 False。"""

    action: MemoryAction
    changed: bool
    duplicate: bool
    document: MemoryDocument
