from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from .permission import PendingApproval, PermissionRequest

ApprovalPendingListener = Callable[[str, PermissionRequest, str], Awaitable[None]]


class ApprovalHandler(ABC):
    """对 ask 决策获取用户批准的可替换处理器。"""

    @abstractmethod
    async def approve(
        self,
        request: PermissionRequest,
        reason: str,
        on_pending: ApprovalPendingListener | None = None,
    ) -> bool:
        raise NotImplementedError


class ApprovalCenter(ApprovalHandler):
    """支持查询和处理待审批请求的审批中心。"""

    @abstractmethod
    async def list_pending(self) -> tuple[PendingApproval, ...]:
        raise NotImplementedError

    @abstractmethod
    async def resolve(self, request_id: str, approved: bool) -> bool:
        raise NotImplementedError
