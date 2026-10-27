"""隔离子 Agent 委派：运行机器与可选委派工具。"""

from .creation import build_subagent
from .runner import LocalSubagentRunner
from .tool import DelegateTaskTool, SubagentToolProvider
from .types import (
    SubagentBuilder,
    SubagentProviderFactory,
    SubagentRequest,
    SubagentResult,
    SubagentRunner,
    SubagentType,
)

__all__ = [
    "build_subagent",
    "SubagentBuilder",
    "DelegateTaskTool",
    "LocalSubagentRunner",
    "SubagentProviderFactory",
    "SubagentRequest",
    "SubagentResult",
    "SubagentRunner",
    "SubagentToolProvider",
    "SubagentType",
]
