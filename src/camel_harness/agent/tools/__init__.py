"""Agent 核心工具协议、注册与执行。"""

from .executor import ToolExecutor
from .parameters import PydanticTool, ToolArguments
from .permissions import (
    ApprovalHandler,
    ApprovalPendingListener,
    PendingApproval,
    PermissionAction,
    PermissionDecision,
    PermissionRequest,
    PermissionService,
    default_permission_decision,
)
from .registry import ToolProvider, ToolRegistry
from .types import (
    PermissionMode,
    PreparedToolCall,
    Tool,
    ToolCall,
    ToolContext,
    ToolExecutionStatus,
    ToolNotExecutedError,
    ToolResult,
    ToolRisk,
)

__all__ = [
    "ApprovalHandler",
    "ApprovalPendingListener",
    "default_permission_decision",
    "PendingApproval",
    "PermissionAction",
    "PermissionDecision",
    "PermissionRequest",
    "PermissionService",
    "PermissionMode",
    "PreparedToolCall",
    "PydanticTool",
    "Tool",
    "ToolArguments",
    "ToolCall",
    "ToolContext",
    "ToolExecutionStatus",
    "ToolNotExecutedError",
    "ToolExecutor",
    "ToolProvider",
    "ToolResult",
    "ToolRegistry",
    "ToolRisk",
]
