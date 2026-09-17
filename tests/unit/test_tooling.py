from backend.tools import ToolExecutor, ToolRegistry
from backend.tools.builtin import BuiltinToolProvider
from backend.tools.jsonschema_validator import validate_tool_arguments
from backend.types import Tool, ToolCall, ToolContext, ToolResult


class DeferredTool(Tool):
    namespace = "plugin"
    name = "browser_fetch"
    description = "抓取网页内容"
    parameters = {"type": "object"}
    tags = frozenset({"browser", "web"})
    deferred = True

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        return ToolResult(call.call_id, call.name, "ok")


def test_deferred_tool_is_activated_by_search() -> None:
    registry = ToolRegistry()
    registry.register(DeferredTool())
    assert registry.llm_definitions() == ()
    found = registry.search_and_activate("browser")
    assert found[0].qualified_name == "plugin__browser_fetch"
    assert registry.llm_definitions()[0].qualified_name == "plugin__browser_fetch"


async def test_builtin_tools_implement_abc_contract() -> None:
    tools = await BuiltinToolProvider().discover()

    assert tools
    assert all(isinstance(tool, Tool) for tool in tools)


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
