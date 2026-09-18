from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[2] / "src"
PACKAGE_ROOT = SOURCE_ROOT / "camel_harness"


def test_harness_is_split_into_agent_and_llm_packages() -> None:
    """Harness 只保留职责清晰的 Agent 与 LLM 两个包。"""

    assert (PACKAGE_ROOT / "agent").is_dir()
    assert (PACKAGE_ROOT / "llm").is_dir()
    assert not (PACKAGE_ROOT / "harness").exists()
    assert not (PACKAGE_ROOT / "types").exists()
    agent_files = {"agent.py", "agent_loop.py", "events.py", "state.py", "runtime.py"}
    assert all((PACKAGE_ROOT / "agent" / name).is_file() for name in agent_files)


def test_backend_and_cli_are_separate_packages() -> None:
    """应用入口不得放进可复用的 camel_harness 包。"""

    assert not (PACKAGE_ROOT / "backend").exists()
    assert not (PACKAGE_ROOT / "cli").exists()
    assert (SOURCE_ROOT / "camel_backend").is_dir()
    assert (SOURCE_ROOT / "camel_cli").is_dir()
    assert not (PACKAGE_ROOT / "agent/workspace.py").exists()
    assert (SOURCE_ROOT / "camel_backend/workspace.py").is_file()


def test_entry_packages_do_not_import_llm_directly() -> None:
    """入口必须经 Agent 使用 LLM，不能绕过产品边界。"""

    forbidden = {"camel_harness.llm"}
    assert not _forbidden_imports(SOURCE_ROOT / "camel_backend", forbidden)
    assert not _forbidden_imports(SOURCE_ROOT / "camel_cli", forbidden)


def test_related_small_contracts_stay_consolidated() -> None:
    """同一变化原因的小型协议不得再次拆成空洞文件。"""

    removed_fragments = {
        "llm/base.py",
        "llm/request.py",
        "llm/stream_event.py",
        "llm/event_factory.py",
        "agent/tools/approval.py",
        "agent/tools/default_permission.py",
        "agent/tools/permission.py",
        "agent/tools/permission_service.py",
        "agent/tools/provider.py",
        "agent/tools/jsonschema_validator.py",
        "agent/compaction/result.py",
        "agent/resources/memory_types.py",
        "agent/resources/skill_types.py",
    }
    assert not [name for name in removed_fragments if (PACKAGE_ROOT / name).exists()]


def test_feature_modules_do_not_restore_ddd_layers() -> None:
    """功能模块内不得机械创建 DDD 分层目录。"""

    forbidden = {"models", "services", "ports", "domain", "infra", "application"}
    violations = [
        path for path in SOURCE_ROOT.rglob("*") if path.is_dir() and path.name in forbidden
    ]
    assert not violations


def test_tool_contract_lives_in_agent_tools() -> None:
    """工具运行契约属于 Agent，模型工具定义属于 LLM。"""

    tool_tree = ast.parse((PACKAGE_ROOT / "agent/tools/types.py").read_text("utf-8"))
    names = {node.name for node in tool_tree.body if isinstance(node, ast.ClassDef)}
    assert {"Tool", "ToolCall", "ToolContext", "ToolResult"} <= names
    protocol_tree = ast.parse((PACKAGE_ROOT / "llm/protocol.py").read_text("utf-8"))
    protocol_names = {node.name for node in protocol_tree.body if isinstance(node, ast.ClassDef)}
    assert "ToolDefinition" in protocol_names


def test_llm_does_not_depend_on_agent_or_entry_packages() -> None:
    """LLM 是独立底层包，不能反向依赖 Agent 或入口层。"""

    forbidden = {"camel_harness.agent", "camel_backend", "camel_cli"}
    assert not _forbidden_imports(PACKAGE_ROOT / "llm", forbidden)


def test_llm_protocol_does_not_contain_agent_runtime_coordinates() -> None:
    """LLM 请求与事件不得感知 Session、Turn、Step 或 Agent 序号。"""

    tree = ast.parse((PACKAGE_ROOT / "llm/protocol.py").read_text("utf-8"))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}

    def fields(name: str) -> set[str]:
        return {
            node.target.id
            for node in classes[name].body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }

    assert fields("LlmRequest") == {
        "model",
        "messages",
        "system_prompt",
        "tools",
        "max_output_tokens",
        "temperature",
        "thinking",
    }
    assert fields("LlmStreamEvent") == {"type", "data"}
    assert fields("ToolDefinition") == {"name", "description", "input_schema"}
    assert not (PACKAGE_ROOT / "llm/message_codec.py").exists()


def test_llm_message_is_a_discriminated_union() -> None:
    """规范消息用独立类型表达合法形状，不恢复通用 role/content 容器。"""

    tree = ast.parse((PACKAGE_ROOT / "llm/message.py").read_text("utf-8"))
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    aliases = {
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }

    assert {"UserMessage", "AssistantMessage", "ToolResultMessage"} <= classes
    assert "Message" in aliases
    assert "Message" not in classes
    assert "MessageRole" not in classes
    assistant = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AssistantMessage"
    )
    assistant_fields = {
        node.target.id
        for node in assistant.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert assistant_fields == {"content", "stop_reason", "role"}


def test_agent_does_not_depend_on_entry_packages() -> None:
    """完整 Agent 不能依赖 HTTP 或 CLI 实现。"""

    assert not _forbidden_imports(PACKAGE_ROOT / "agent", {"camel_backend", "camel_cli"})


def test_agent_loop_does_not_depend_on_product_state() -> None:
    """Agent Loop 只处理模型、工具、预算和流事件。"""

    forbidden = {
        "camel_harness.agent.compaction",
        "camel_harness.agent.config",
        "camel_harness.agent.resources",
        "camel_harness.agent.runtime",
        "camel_harness.agent.session",
    }
    assert not _forbidden_imports(PACKAGE_ROOT / "agent/agent_loop.py", forbidden)


def test_agent_loop_is_a_function_and_agent_surface_stays_minimal() -> None:
    """低层循环保持无状态函数，Agent 只暴露运行与关闭。"""

    loop_tree = ast.parse((PACKAGE_ROOT / "agent/agent_loop.py").read_text("utf-8"))
    assert any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "agent_loop"
        for node in loop_tree.body
    )
    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "AgentLoop" for node in loop_tree.body
    )

    agent_tree = ast.parse((PACKAGE_ROOT / "agent/agent.py").read_text("utf-8"))
    agent_class = next(
        node for node in agent_tree.body if isinstance(node, ast.ClassDef) and node.name == "Agent"
    )
    public_methods = {
        node.name
        for node in agent_class.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }
    assert public_methods == {"run_turn", "close"}


def test_runtime_is_the_only_full_composition_module() -> None:
    """完整对象图只在 Agent 的 runtime 中装配。"""

    assert (PACKAGE_ROOT / "agent/runtime.py").is_file()
    assert not (PACKAGE_ROOT / "runtime.py").exists()
    assert not (PACKAGE_ROOT / "agent/bootstrap").exists()


def _forbidden_imports(root: Path, forbidden: set[str]) -> list[str]:
    violations: list[str] = []
    paths = [root] if root.is_file() else root.rglob("*.py")
    for path in paths:
        for module in _absolute_imports(path):
            if any(module == item or module.startswith(f"{item}.") for item in forbidden):
                violations.append(f"{path.relative_to(SOURCE_ROOT)}: {module}")
    return violations


def _absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules
