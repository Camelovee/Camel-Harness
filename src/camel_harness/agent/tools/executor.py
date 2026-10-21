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
    """统一工具定义查询、参数验证、授权、超时和异常归一化。"""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        permission_service: PermissionService | None = None,
        normalize_result: ResultNormalizer | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._permission_service = permission_service or PermissionService()
        self._normalize_result = normalize_result

    def llm_definitions(self) -> tuple[ToolDefinition, ...]:
        """从实际执行使用的注册表查询定义，包含组装后追加的工具。"""

        return self._tool_registry.llm_definitions()

    async def execute(
        self,
        tool_call: ToolCall,
        tool_context: ToolContext,
        on_approval: ApprovalPendingListener | None = None,
    ) -> ToolResult:
        try:
            tool = self._tool_registry.resolve(tool_call.name)
        except KeyError as exc:
            return ToolResult(tool_call.call_id, tool_call.name, str(exc), is_error=True)

        try:
            errors = validate_tool_arguments(tool.to_definition(), tool_call.arguments)
        except Exception as exc:
            return ToolResult(
                tool_call.call_id, tool_call.name, f"工具参数校验失败：{exc}", is_error=True
            )
        if errors:
            return ToolResult(tool_call.call_id, tool_call.name, "；".join(errors), is_error=True)
        permission_request = PermissionRequest(
            tool_name=tool_call.name,
            capabilities=tool.capabilities,
            risk=tool.risk.value,
            arguments=tool_call.arguments,
            mode=tool_context.permission_mode,
            request_id=uuid4().hex,
            session_id=tool_context.session_id,
        )
        try:
            decision = await self._permission_service.authorize(permission_request, on_approval)
        except Exception as exc:
            return ToolResult(
                tool_call.call_id, tool_call.name, f"工具审批失败：{exc}", is_error=True
            )
        if decision.action is PermissionAction.DENY:
            return ToolResult(
                tool_call.call_id, tool_call.name, f"权限拒绝：{decision.reason}", is_error=True
            )
        try:
            result = await asyncio.wait_for(
                tool.execute(tool_call, tool_context), timeout=tool.timeout_seconds
            )
        except TimeoutError:
            return ToolResult(tool_call.call_id, tool_call.name, "工具执行超时", is_error=True)
        except ToolResultStorageError:
            raise
        except Exception as exc:
            return ToolResult(
                tool_call.call_id, tool_call.name, f"工具执行失败：{exc}", is_error=True
            )
        # 执行可能已产生副作用；归档失败属于基础设施故障，不让模型误以为工具未执行。
        if self._normalize_result is not None:
            result = await self._normalize_result(result, tool_context)
        return result
