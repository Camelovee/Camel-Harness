from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class SessionId:
    """会话标识，使用值对象避免不同类型的 ID 被误传。"""

    value: str

    @classmethod
    def new(cls) -> SessionId:
        return cls(uuid4().hex)


@dataclass(frozen=True, slots=True)
class TurnId:
    """一次用户输入及其后续工具循环的标识。"""

    value: str

    @classmethod
    def new(cls) -> TurnId:
        return cls(uuid4().hex)


@dataclass(frozen=True, slots=True)
class StepId:
    """单次模型请求的标识。"""

    value: str

    @classmethod
    def new(cls) -> StepId:
        return cls(uuid4().hex)
