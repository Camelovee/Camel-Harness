from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .types import PermissionMode


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
    execution_context: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PendingApproval:
    """等待外部用户处理的审批请求。"""

    request_id: str
    tool_name: str
    capabilities: frozenset[str]
    risk: str
    arguments: dict[str, Any]
    reason: str
    session_id: str = ""
    execution_context: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    """工具授权决策及其可审计原因。"""

    action: PermissionAction
    reason: str


ApprovalPendingListener = Callable[[str, PermissionRequest, str], Awaitable[None]]


class ApprovalHandler(ABC):
    """对 ask 决策获取用户批准的可替换处理器。"""

    @abstractmethod
    async def approve(
        self,
        permission_request: PermissionRequest,
        reason: str,
        on_pending: ApprovalPendingListener | None = None,
    ) -> bool:
        raise NotImplementedError


def default_permission_decision(permission_request: PermissionRequest) -> PermissionDecision:
    """提供保守且可预测的本地工具授权默认策略。"""

    if "memory.write" in permission_request.capabilities:
        if permission_request.mode is PermissionMode.PLAN:
            return PermissionDecision(PermissionAction.DENY, "Plan Mode 禁止修改长期记忆")
        return PermissionDecision(
            PermissionAction.ASK,
            "长期记忆修改必须确认层级和实际内容；用户级记忆影响其他项目",
        )
    if permission_request.mode is PermissionMode.YOLO:
        return PermissionDecision(PermissionAction.ALLOW, "YOLO 模式允许所有已注册工具")
    if permission_request.risk == "read":
        return PermissionDecision(PermissionAction.ALLOW, "只读能力自动允许")
    if permission_request.mode is PermissionMode.PLAN:
        return PermissionDecision(PermissionAction.DENY, "Plan Mode 禁止产生副作用")
    if (
        permission_request.mode is PermissionMode.ACCEPT_EDITS
        and permission_request.risk == "write"
    ):
        return PermissionDecision(PermissionAction.ALLOW, "接受编辑模式允许工作区写入")
    return PermissionDecision(PermissionAction.ASK, "该工具可能修改环境，需要用户确认")


class PermissionService:
    """统一完成工具授权决策与审批。"""

    def __init__(
        self,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self._approval_handler = approval_handler

    async def authorize(
        self,
        permission_request: PermissionRequest,
        on_pending: ApprovalPendingListener | None = None,
    ) -> PermissionDecision:
        """返回包含审批结果的最终权限决策。"""

        decision = default_permission_decision(permission_request)
        if decision.action is not PermissionAction.ASK:
            return decision
        if self._approval_handler is None:
            return PermissionDecision(PermissionAction.DENY, "权限请求需要审批但没有可用审批服务")
        approved = await self._approval_handler.approve(
            permission_request, decision.reason, on_pending
        )
        if approved:
            return PermissionDecision(PermissionAction.ALLOW, decision.reason)
        return PermissionDecision(PermissionAction.DENY, "用户未批准工具执行")
