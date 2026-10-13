"""隔离子 Agent 委派：运行机器与可选委派工具。"""

from .runner import (
    LocalSubagentRunner,
    SubagentProviderFactory,
    SubagentRequest,
    SubagentResult,
    SubagentRunner,
    SubagentType,
)
from .tool import DelegateTaskTool, SubagentToolProvider

__all__ = [
    "DelegateTaskTool",
    "LocalSubagentRunner",
    "SubagentProviderFactory",
    "SubagentRequest",
    "SubagentResult",
    "SubagentRunner",
    "SubagentToolProvider",
    "SubagentType",
]
