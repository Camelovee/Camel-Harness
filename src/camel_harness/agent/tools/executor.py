from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

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
    PreparedToolCall,
    Tool,
    ToolCall,
    ToolContext,
    ToolExecutionStatus,
    ToolNotExecutedError,
    ToolResult,
    ToolRisk,
)

ResultNormalizer = Callable[[ToolResult, ToolContext], Awaitable[ToolResult]]


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
        # 参数深拷贝：审批快照与执行各持一份，工具实现无法原地改写审批看到的内容。
        tool_call = replace(tool_call, arguments=deepcopy(tool_call.arguments))
        try:
            # 审批与执行必须使用同一规范化工作区，否则展示路径和实际操作可能不同。
            tool_context = replace(tool_context, workspace=tool_context.workspace.resolve())
            tool = self._tool_registry.resolve(tool_call.name)
        except Exception as exc:
            return _error_result(tool_call, str(exc), ToolExecutionStatus.NOT_EXECUTED)

        try:
            prepared_call = tool.prepare_call(tool_call, tool_context)
        except ToolNotExecutedError as exc:
            return _error_result(tool_call, str(exc), ToolExecutionStatus.NOT_EXECUTED)
        except Exception as exc:
            return _error_result(
                tool_call, f"工具参数或执行上下文校验失败：{exc}", ToolExecutionStatus.NOT_EXECUTED
            )
        permission_request = PermissionRequest(
            tool_name=tool_call.name,
            capabilities=tool.capabilities,
            risk=tool.risk.value,
            # 审批后参数即固定；执行使用同一份深拷贝快照。
            arguments=deepcopy(prepared_call.arguments),
            mode=tool_context.permission_mode,
            request_id=uuid4().hex,
            session_id=tool_context.session_id,
            execution_context=prepared_call.execution_context,
        )
        try:
            decision = await self._permission_service.authorize(permission_request, on_approval)
        except Exception as exc:
            return _error_result(
                tool_call, f"工具审批失败：{exc}", ToolExecutionStatus.NOT_EXECUTED
            )
        if decision.action is PermissionAction.DENY:
            return _error_result(
                tool_call, f"权限拒绝：{decision.reason}", ToolExecutionStatus.NOT_EXECUTED
            )
        return await self._execute_authorized(tool, tool_call, tool_context, prepared_call)

    async def _execute_authorized(
        self,
        tool: Tool,
        tool_call: ToolCall,
        tool_context: ToolContext,
        prepared_call: PreparedToolCall,
    ) -> ToolResult:
        """执行后无法确认的副作用保守标记未知，取消与归档故障继续传播。"""

        try:
            # wait_for 只取消协程；线程或子进程可能仍在运行，超时只能是 UNKNOWN。
            result = await asyncio.wait_for(prepared_call.execute(), timeout=tool.timeout_seconds)
        except ToolNotExecutedError as exc:
            result = _error_result(tool_call, str(exc), ToolExecutionStatus.NOT_EXECUTED)
        except TimeoutError:
            result = _error_result(tool_call, "工具执行超时", ToolExecutionStatus.UNKNOWN)
        except ToolResultStorageError:
            # 结果归档失败发生在工具执行之后，不能降级成“未执行”。
            raise
        except Exception as exc:
            # 只有 READ 能确定失败；其余风险可能已经产生副作用，保守判 UNKNOWN。
            status = (
                ToolExecutionStatus.FAILED
                if tool.risk is ToolRisk.READ
                else ToolExecutionStatus.UNKNOWN
            )
            result = _error_result(tool_call, f"工具执行失败：{exc}", status)
        # metadata 里的显式状态优先；is_error 由最终状态反推，而不是反过来。
        status = ToolExecutionStatus(
            result.metadata.get(
                "execution_status",
                ToolExecutionStatus.FAILED if result.is_error else ToolExecutionStatus.SUCCEEDED,
            )
        )
        result = result.with_execution_status(status)
        # 归档失败会抛 ToolResultStorageError 中止 Run；成功时保持执行状态不变。
        if self._normalize_result is not None:
            result = await self._normalize_result(result, tool_context)
        return result


def _error_result(tool_call: ToolCall, content: str, status: ToolExecutionStatus) -> ToolResult:
    """所有拒绝和异常都保留执行状态，未知结果带明确核验提示。"""

    return ToolResult(
        tool_call.call_id, tool_call.name, content, is_error=True
    ).with_execution_status(status)
