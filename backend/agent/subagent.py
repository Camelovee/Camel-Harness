from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from backend.types import PermissionMode


class SubagentType(StrEnum):
    """内置子 Agent 委派预设。"""

    EXPLORE = "explore"
    PLAN = "plan"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class SubagentRequest:
    """父 Agent 发起的一次隔离委派。"""

    parent_session_id: str
    task: str
    agent_type: SubagentType
    workspace: Path
    permission_mode: PermissionMode


@dataclass(frozen=True, slots=True)
class SubagentResult:
    """被委派 Agent 回传给父 Agent 的最终文本。"""

    session_id: str
    content: str
    succeeded: bool
