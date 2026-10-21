from pathlib import Path

import pytest

from camel_harness.agent.subagent import (
    SubagentRequest,
    SubagentResult,
    SubagentToolProvider,
)
from camel_harness.agent.tools import (
    PermissionMode,
    PermissionService,
    Tool,
    ToolCall,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
)
from camel_harness.agent.tools.builtin import BuiltinToolProvider
from camel_harness.llm import LlmModel

_MODEL = LlmModel("fake", "fake-model")


class ExampleTool(Tool):
    namespace = "plugin"
    name = "browser_fetch"
    description = "抓取网页内容"
    parameters = {"type": "object"}

    async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
        del tool_context
        return ToolResult(tool_call.call_id, tool_call.name, "ok")


def test_tool_result_projects_message_without_sharing_metadata():
    tool_result = ToolResult("call", "read_file", "结果", is_error=True, metadata={"exit_code": 1})
    message = tool_result.to_message()
    assert message.call_id == tool_result.call_id
    assert message.name == tool_result.name
    assert message.content == tool_result.content
    assert message.is_error
    assert message.details == tool_result.metadata
    assert message.details is not tool_result.metadata


async def test_executor_default_permissions_do_not_bypass_plan_mode(tmp_path):
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    tool_result = await ToolExecutor(tool_registry=tool_registry).execute(
        ToolCall("write", "write_file", {"path": "blocked.txt", "content": "不能写入"}),
        ToolContext("session", tmp_path, PermissionMode.PLAN),
    )
    assert tool_result.is_error
    assert "权限拒绝" in tool_result.content
    assert not (tmp_path / "blocked.txt").exists()


async def test_executor_approval_exception_is_an_error_result(tmp_path):
    class BrokenPermission(PermissionService):
        async def authorize(self, permission_request, on_pending=None):
            raise OSError("审批不可用")

    tool_registry = ToolRegistry()
    tool_registry.register(ExampleTool())
    tool_result = await ToolExecutor(
        tool_registry=tool_registry, permission_service=BrokenPermission()
    ).execute(ToolCall("call", "plugin__browser_fetch", {}), ToolContext("session", tmp_path))
    assert tool_result.is_error
    assert "审批不可用" in tool_result.content


async def test_result_storage_failure_is_not_reported_as_tool_execution_failure(tmp_path):
    async def failed_storage(result, context):
        raise OSError("结果归档失败")

    tool_registry = ToolRegistry()
    tool_registry.register(ExampleTool())
    with pytest.raises(OSError, match="结果归档失败"):
        await ToolExecutor(tool_registry=tool_registry, normalize_result=failed_storage).execute(
            ToolCall("call", "plugin__browser_fetch", {}), ToolContext("session", tmp_path)
        )


def test_registered_tool_is_exposed_directly() -> None:
    tool_registry = ToolRegistry()
    tool_registry.register(ExampleTool())

    assert tool_registry.llm_definitions()[0].name == "plugin__browser_fetch"


async def test_executor_definitions_follow_the_registry_used_for_execution(tmp_path: Path) -> None:
    tool_registry = ToolRegistry()
    tool_executor = ToolExecutor(tool_registry=tool_registry)
    assert tool_executor.llm_definitions() == ()

    tool_registry.register(ExampleTool())
    tool_definitions = tool_executor.llm_definitions()
    assert [definition.name for definition in tool_definitions] == ["plugin__browser_fetch"]
    tool_result = await tool_executor.execute(
        ToolCall("call", tool_definitions[0].name, {}), ToolContext("session", tmp_path)
    )

    assert tool_result.name == tool_definitions[0].name
    assert tool_result.content == "ok"
    assert not tool_result.is_error


async def test_builtin_tools_implement_abc_contract() -> None:
    tools = await BuiltinToolProvider().discover()

    assert {tool.name for tool in tools} == {"read_file", "write_file", "edit_file", "run_shell"}
    assert all(isinstance(tool, Tool) for tool in tools)


async def test_subagent_provider_exposes_only_delegate_tool() -> None:
    requests: list[SubagentRequest] = []

    async def run(request: SubagentRequest) -> SubagentResult:
        requests.append(request)
        return SubagentResult("sub-session", request.task, True)

    tools = await SubagentToolProvider(run).discover()

    assert [tool.name for tool in tools] == ["delegate_task"]

    tool_result = await tools[0].execute(
        ToolCall("call-1", "delegate_task", {"task": "检查代码", "agent_type": "explore"}),
        ToolContext("parent-session", Path("/tmp"), model=_MODEL),
    )
    assert requests[0].parent_session_id == "parent-session"
    assert requests[0].parent_tool_call_id == "call-1"
    assert requests[0].model is _MODEL
    assert tool_result.metadata["subagent_session_id"] == "sub-session"


async def test_invalid_tool_arguments_are_normalized(tmp_path) -> None:
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    tool_executor = ToolExecutor(tool_registry=tool_registry)

    tool_result = await tool_executor.execute(
        ToolCall("call-1", "read_file", {}),
        ToolContext("session-1", tmp_path),
    )

    assert tool_result.is_error
    assert "required" in tool_result.content
