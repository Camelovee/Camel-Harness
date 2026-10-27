from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from camel_harness.llm import LlmModel

from ..agent import Agent
from ..tools import PermissionMode, ToolProvider
from ..tools.types import SubagentObserver


class SubagentType(StrEnum):
    """内置子 Agent 的最小任务预设。"""

    EXPLORE = "explore"
    PLAN = "plan"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class SubagentRequest:
    """父 Agent 发起的一次隔离委派。"""

    task: str
    agent_type: SubagentType
    workspace: Path
    permission_mode: PermissionMode
    parent_session_id: str
    parent_tool_call_id: str
    model: LlmModel
    on_event: SubagentObserver | None = None


@dataclass(frozen=True, slots=True)
class SubagentResult:
    """被委派 Agent 回传给父 Agent 的最终文本。"""

    session_id: str
    content: str
    succeeded: bool


SubagentRunner = Callable[[SubagentRequest], Awaitable[SubagentResult]]
SubagentProviderFactory = Callable[[SubagentRunner], ToolProvider]

SubagentBuilder = Callable[[SubagentRequest], Awaitable[Agent]]
