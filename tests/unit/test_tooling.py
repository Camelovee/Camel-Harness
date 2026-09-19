from camel_harness.agent.tools import (
    Tool,
    ToolCall,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    validate_tool_arguments,
)
from camel_harness.agent.tools.builtin import BuiltinToolProvider
from camel_harness.agent.tools.subagent import (
    SubagentRequest,
    SubagentResult,
    SubagentToolProvider,
)


class ExampleTool(Tool):
    namespace = "plugin"
    name = "browser_fetch"
    description = "抓取网页内容"
    parameters = {"type": "object"}

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        return ToolResult(call.call_id, call.name, "ok")


def test_registered_tool_is_exposed_directly() -> None:
    registry = ToolRegistry()
    registry.register(ExampleTool())

    assert registry.llm_definitions()[0].name == "plugin__browser_fetch"


async def test_builtin_tools_implement_abc_contract() -> None:
    tools = await BuiltinToolProvider().discover()

    assert {tool.name for tool in tools} == {"read_file", "write_file", "edit_file", "run_shell"}
    assert all(isinstance(tool, Tool) for tool in tools)


async def test_subagent_provider_exposes_only_delegate_tool() -> None:
    async def run(request: SubagentRequest) -> SubagentResult:
        return SubagentResult("sub-session", request.task, True)

    tools = await SubagentToolProvider(run).discover()

    assert [tool.name for tool in tools] == ["delegate_task"]


async def test_invalid_tool_arguments_are_normalized(tmp_path) -> None:
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    executor = ToolExecutor(registry, validate_tool_arguments)

    result = await executor.execute(
        ToolCall("call-1", "read_file", {}),
        ToolContext("session-1", tmp_path),
    )

    assert result.is_error
    assert "required" in result.content
