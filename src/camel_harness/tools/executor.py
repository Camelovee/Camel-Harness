from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import uuid4

from camel_harness.types import ToolArgumentValidator, ToolCall, ToolContext, ToolResult

from .approval import ApprovalPendingListener
from .permission import PermissionAction, PermissionRequest
from .permission_service import PermissionService
from .registry import ToolRegistry

ResultNormalizer = Callable[[ToolResult, ToolContext], Awaitable[ToolResult]]


class ToolExecutor:
    """统一执行参数验证、授权、超时和异常归一化。"""

    def __init__(
        self,
        registry: ToolRegistry,
        validate_arguments: ToolArgumentValidator,
        permission: PermissionService | None = None,
        normalize_result: ResultNormalizer | None = None,
    ) -> None:
        self._registry = registry
        self._validate_arguments = validate_arguments
        self._permission = permission
        self._normalize_result = normalize_result

    async def execute(
        self,
        call: ToolCall,
        context: ToolContext,
        on_approval: ApprovalPendingListener | None = None,
    ) -> ToolResult:
        try:
            tool = self._registry.resolve(call.name)
        except KeyError as exc:
            return ToolResult(call.call_id, call.name, str(exc), is_error=True)

        errors = self._validate_arguments(tool.to_definition(), call.arguments)
        if errors:
            return ToolResult(call.call_id, call.name, "；".join(errors), is_error=True)
        if self._permission is not None:
            request = PermissionRequest(
                tool_name=call.name,
                capabilities=tool.capabilities,
                risk=tool.risk.value,
                arguments=call.arguments,
                mode=context.permission_mode,
                request_id=uuid4().hex,
                session_id=context.session_id,
            )
            decision = await self._permission.authorize(request, on_approval)
            if decision.action is PermissionAction.DENY:
                return ToolResult(
                    call.call_id, call.name, f"权限拒绝：{decision.reason}", is_error=True
                )
        try:
            result = await asyncio.wait_for(
                tool.execute(call, context), timeout=tool.timeout_seconds
            )
            if self._normalize_result is not None:
                result = await self._normalize_result(result, context)
            return result
        except TimeoutError:
            return ToolResult(call.call_id, call.name, "工具执行超时", is_error=True)
        except Exception as exc:
            return ToolResult(call.call_id, call.name, f"工具执行失败：{exc}", is_error=True)
