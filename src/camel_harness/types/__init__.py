"""跨模块共享的稳定基础类型。"""

from .identifiers import SessionId, StepId, TurnId
from .message import (
    ContentBlock,
    Message,
    MessageRole,
    ReasoningBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from .serialization import message_from_dict, message_to_dict
from .tool import (
    PermissionMode,
    Tool,
    ToolArgumentValidator,
    ToolCall,
    ToolContext,
    ToolDefinition,
    ToolResult,
    ToolRisk,
)

__all__ = [
    "ContentBlock",
    "Message",
    "MessageRole",
    "PermissionMode",
    "ReasoningBlock",
    "SessionId",
    "StepId",
    "TextBlock",
    "Tool",
    "ToolArgumentValidator",
    "ToolCall",
    "ToolContext",
    "ToolDefinition",
    "ToolResult",
    "ToolResultBlock",
    "ToolRisk",
    "ToolUseBlock",
    "TurnId",
    "message_from_dict",
    "message_to_dict",
]
