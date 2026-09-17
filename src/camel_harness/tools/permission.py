from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from camel_harness.types import PermissionMode


class PermissionAction(StrEnum):
    """授权策略对一次能力请求作出的动作。"""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """工具执行前提交给授权策略的不可变请求。"""

    tool_name: str
    capabilities: frozenset[str]
    risk: str
    arguments: dict[str, Any]
    mode: PermissionMode
    request_id: str = ""
    session_id: str = ""


@dataclass(frozen=True, slots=True)
class PendingApproval:
    """等待外部用户处理的审批请求；不携带可变执行状态。"""

    request_id: str
    tool_name: str
    capabilities: frozenset[str]
    risk: str
    arguments: dict[str, Any]
    reason: str
    session_id: str = ""


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    """工具授权决策及其可审计原因。"""

    action: PermissionAction
    reason: str
