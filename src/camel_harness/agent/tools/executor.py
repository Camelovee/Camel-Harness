from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import uuid4

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from camel_harness.llm import ToolDefinition

from .file_result_storage import ToolResultStorageError
from .permissions import (
    ApprovalPendingListener,
    PermissionAction,
    PermissionRequest,
    PermissionService,
)
from .registry import ToolRegistry
from .types import (
    ToolCall,
    ToolContext,
    ToolResult,
)

ResultNormalizer = Callable[[ToolResult, ToolContext], Awaitable[ToolResult]]


def validate_tool_arguments(
    definition: ToolDefinition,
    arguments: dict[str, object],
) -> tuple[str, ...]:
    """使用 Draft 2020-12 校验一组工具参数。"""

    errors = Draft202012Validator(definition.input_schema).iter_errors(arguments)
    return tuple(error.message for error in sorted(errors, key=lambda item: list(item.path)))


class ToolExecutor:
    """统一执行参数验证、授权、超时和异常归一化。"""

    def __init__(
        self,
        registry: ToolRegistry,
        permission: PermissionService | None = None,
        normalize_result: ResultNormalizer | None = None,
    ) -> None:
        self._registry = registry
        self._permission = permission or PermissionService()
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

        try:
            errors = validate_tool_arguments(tool.to_definition(), call.arguments)
        except Exception as exc:
            return ToolResult(call.call_id, call.name, f"工具参数校验失败：{exc}", is_error=True)
        if errors:
            return ToolResult(call.call_id, call.name, "；".join(errors), is_error=True)
        request = PermissionRequest(
            tool_name=call.name,
            capabilities=tool.capabilities,
            risk=tool.risk.value,
            arguments=call.arguments,
            mode=context.permission_mode,
            request_id=uuid4().hex,
            session_id=context.session_id,
        )
        try:
            decision = await self._permission.authorize(request, on_approval)
        except Exception as exc:
            return ToolResult(call.call_id, call.name, f"工具审批失败：{exc}", is_error=True)
        if decision.action is PermissionAction.DENY:
            return ToolResult(
                call.call_id, call.name, f"权限拒绝：{decision.reason}", is_error=True
            )
        try:
            result = await asyncio.wait_for(
                tool.execute(call, context), timeout=tool.timeout_seconds
            )
        except TimeoutError:
            return ToolResult(call.call_id, call.name, "工具执行超时", is_error=True)
        except ToolResultStorageError:
            raise
        except Exception as exc:
            return ToolResult(call.call_id, call.name, f"工具执行失败：{exc}", is_error=True)
        # 执行可能已产生副作用；归档失败属于基础设施故障，不让模型误以为工具未执行。
        if self._normalize_result is not None:
            result = await self._normalize_result(result, context)
        return result
