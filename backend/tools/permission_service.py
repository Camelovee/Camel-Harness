from __future__ import annotations

from .approval import ApprovalHandler, ApprovalPendingListener
from .permission import (
    PermissionAction,
    PermissionDecision,
    PermissionRequest,
)
from .permission_policy import PermissionPolicy


class PermissionService:
    """统一完成工具授权决策与审批。"""

    def __init__(
        self,
        policy: PermissionPolicy,
        approval: ApprovalHandler | None = None,
    ) -> None:
        self._policy = policy
        self._approval = approval

    async def authorize(
        self,
        request: PermissionRequest,
        on_pending: ApprovalPendingListener | None = None,
    ) -> PermissionDecision:
        """返回包含审批结果的最终权限决策。"""
        decision = self._policy.decide(request)
        if decision.action is not PermissionAction.ASK:
            return decision
        if self._approval is None:
            return PermissionDecision(PermissionAction.DENY, "权限请求需要审批但没有可用审批服务")
        approved = await self._approval.approve(request, decision.reason, on_pending)
        if approved:
            return PermissionDecision(PermissionAction.ALLOW, decision.reason)
        return PermissionDecision(PermissionAction.DENY, "用户未批准工具执行")
