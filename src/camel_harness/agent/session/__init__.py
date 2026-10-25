"""会话状态、上下文重放与 JSONL 存储。"""

from .events import (
    ContextNode,
    ContextSnapshot,
    SessionCompactionEvent,
    SessionEntry,
    SessionEvent,
    SessionHeaderEvent,
    SessionInfoEvent,
    SessionMessageEvent,
    message_from_dict,
    message_to_dict,
)
from .manager import Session, SessionManager
from .resume import ResumePlan, analyze_resume
from .types import SessionMetadata

__all__ = [
    "Session",
    "ContextNode",
    "ContextSnapshot",
    "SessionEvent",
    "SessionEntry",
    "SessionHeaderEvent",
    "SessionMessageEvent",
    "SessionCompactionEvent",
    "SessionInfoEvent",
    "SessionManager",
    "SessionMetadata",
    "ResumePlan",
    "analyze_resume",
    "message_from_dict",
    "message_to_dict",
]
