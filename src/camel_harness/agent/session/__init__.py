"""会话状态、上下文重放与 JSONL 存储。"""

from .codec import message_from_dict, message_to_dict
from .events import (
    ContextNode,
    ContextSnapshot,
    SessionCompactionEvent,
    SessionEntry,
    SessionEvent,
    SessionHeaderEvent,
    SessionInfoEvent,
    SessionMessageEvent,
)
from .manager import Session, SessionManager
from .metadata import SessionMetadata
from .resume import ResumePlan, analyze_resume

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
