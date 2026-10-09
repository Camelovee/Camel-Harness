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
    agent_files = {"agent.py", "events.py", "state.py", "runtime.py"}
    assert all((PACKAGE_ROOT / "agent" / name).is_file() for name in agent_files)
    assert not (PACKAGE_ROOT / "agent/agent_loop.py").exists()


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
        "agent/compaction/context.py",
        "agent/resources/memory_types.py",
        "agent/resources/skill_types.py",
        "agent/tools/knowledge.py",
        "agent/tools/memory.py",
        "agent/tools/plugins.py",
        "agent/tools/search.py",
        "agent/tools/skill.py",
    }
    assert not [name for name in removed_fragments if (PACKAGE_ROOT / name).exists()]
    assert (PACKAGE_ROOT / "agent/tools/subagent.py").is_file()


def test_context_manager_is_separate_from_session_persistence() -> None:
    """ContextManager 位于独立目录，不持有 SessionManager 或 JSONL 存储。"""

    manager_path = PACKAGE_ROOT / "agent/context/manager.py"
    tree = ast.parse(manager_path.read_text("utf-8"))
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    relative_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0 and node.module
    }

    assert "ContextManager" in classes
    assert not (PACKAGE_ROOT / "agent/compaction/context.py").exists()
    assert not list((PACKAGE_ROOT / "agent/compaction").glob("*.py"))
    assert not {"SessionManager", "JsonlSessionStorage"} & identifiers
    assert "AgentOptions" not in identifiers
    assert not {"session.manager", "session.jsonl"} & relative_modules
    compaction_tree = ast.parse((PACKAGE_ROOT / "agent/context/compaction.py").read_text("utf-8"))
    assert not any(
        isinstance(node, ast.Name) and node.id == "AgentOptions"
        for node in ast.walk(compaction_tree)
    )


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


def test_agent_depends_on_bound_session_not_session_manager() -> None:
    """Agent 只拿单个会话的能力，Session 保存接口不反向感知 AgentEvent。"""
    tree = ast.parse((PACKAGE_ROOT / "agent/agent.py").read_text("utf-8"))
    identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "Session" in identifiers
    assert "SessionManager" not in identifiers
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "_session" in attributes
    assert "_sessions" not in attributes
    session_tree = ast.parse((PACKAGE_ROOT / "agent/session/manager.py").read_text("utf-8"))
    classes = {node.name: node for node in session_tree.body if isinstance(node, ast.ClassDef)}
    methods = {
        node.name
        for node in classes["Session"].body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert not {"create", "ensure", "delete", "list", "update", "record_agent_event"} & methods
    session_identifiers = {node.id for node in ast.walk(session_tree) if isinstance(node, ast.Name)}
    assert not {"AgentEvent", "MessageEndEvent", "Agent"} & session_identifiers


def test_session_exposes_snapshot_not_mutable_log() -> None:
    """上下文消费方只读快照，重放和持久化收敛到 Session 包。"""
    assert not (PACKAGE_ROOT / "agent/session/log.py").exists()
    events_tree = ast.parse((PACKAGE_ROOT / "agent/session/events.py").read_text("utf-8"))
    classes = {node.name: node for node in events_tree.body if isinstance(node, ast.ClassDef)}
    assert "SessionLog" not in classes
    assert "SessionLogEvent" not in classes
    assert {
        "ContextSnapshot",
        "SessionHeaderEvent",
        "SessionMessageEvent",
        "SessionCompactionEvent",
        "SessionInfoEvent",
    } <= classes.keys()
    for path in (PACKAGE_ROOT / "agent/context").glob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert not {"SessionLog", "SessionLogEvent", "SessionEvent"} & identifiers
    agent_tree = ast.parse((PACKAGE_ROOT / "agent/agent.py").read_text("utf-8"))
    names = {node.id for node in ast.walk(agent_tree) if isinstance(node, ast.Name)}
    assert not {"SessionEvent", "SessionCompactionEvent", "SessionLog"} & names


def test_default_toolset_stays_small() -> None:
    """默认工具只保留 pi-agent 式的核心文件与 Shell 能力。"""

    tree = ast.parse((PACKAGE_ROOT / "agent/tools/builtin.py").read_text("utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert {"ReadFileTool", "WriteFileTool", "EditFileTool", "RunShellTool"} <= imported
    assert not {"ListFilesTool", "GrepSearchTool"} & imported


def test_llm_does_not_depend_on_agent_or_entry_packages() -> None:
    """LLM 是独立底层包，不能反向依赖 Agent 或入口层。"""

    forbidden = {"camel_harness.agent", "camel_backend", "camel_cli"}
    assert not _forbidden_imports(PACKAGE_ROOT / "llm", forbidden)


def test_llm_protocol_does_not_contain_agent_runtime_coordinates() -> None:
    """LLM 请求与事件不得感知 Session、Run、Turn 或 Agent 序号。"""

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
    expected_events = {
        "StreamStarted": {"type", "model"},
        "TextDelta": {"type", "text"},
        "ReasoningDelta": {"type", "text"},
        "ToolCallStarted": {"type", "call_id", "name"},
        "ToolCallArgumentsDelta": {"type", "call_id", "delta"},
        "ToolCallCompleted": {"type", "tool_call"},
        "UsageUpdated": {"type", "usage"},
        "MessageCompleted": {"type", "stop_reason", "model"},
        "StreamFailed": {"type", "error", "retryable"},
    }
    assert "LlmStreamEvent" not in classes
    for name, expected in expected_events.items():
        assert fields(name) == expected
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
    assert assistant_fields == {
        "content",
        "stop_reason",
        "role",
        "provider",
        "model",
        "response_model",
        "usage",
    }


def test_agent_does_not_depend_on_entry_packages() -> None:
    """完整 Agent 不能依赖 HTTP 或 CLI 实现。"""

    assert not _forbidden_imports(PACKAGE_ROOT / "agent", {"camel_backend", "camel_cli"})


def test_agent_owns_loop_and_keeps_public_surface_minimal() -> None:
    """ReAct 循环由 Agent 内部编排，不再建立独立 loop 入口。"""

    agent_tree = ast.parse((PACKAGE_ROOT / "agent/agent.py").read_text("utf-8"))
    assert not any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "agent_loop"
        for node in agent_tree.body
    )
    agent_class = next(
        node for node in agent_tree.body if isinstance(node, ast.ClassDef) and node.name == "Agent"
    )
    public_methods = {
        node.name
        for node in agent_class.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }
    assert public_methods == {
        "abort",
        "close",
        "is_running",
        "resume",
        "run",
        "session_id",
        "steer",
        "subscribe",
        "wait_for_idle",
        "workspace",
    }
    private_methods = {
        node.name
        for node in agent_class.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("_")
    }
    assert {"_run_turn", "_prepare_context", "_execute_calls"} <= private_methods
    assert "_load_context" not in private_methods
    assert "_save_message" not in private_methods


def test_agent_events_are_a_lifecycle_union() -> None:
    """Agent 事件用判别联合表达生命周期，不恢复通用 data 容器。"""

    tree = ast.parse((PACKAGE_ROOT / "agent/events.py").read_text("utf-8"))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    aliases = {
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }

    assert "AgentEvent" in aliases
    assert "AgentStreamEvent" not in classes
    assert {
        "AgentStartEvent",
        "AgentEndEvent",
        "TurnStartEvent",
        "TurnEndEvent",
        "MessageStartEvent",
        "MessageUpdateEvent",
        "MessageEndEvent",
        "ToolExecutionStartEvent",
        "ToolExecutionEndEvent",
        "ApprovalRequestedEvent",
    } <= classes.keys()
    assert all(
        "data"
        not in {
            node.target.id
            for node in event_class.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        for event_class in classes.values()
    )


def test_agent_loop_is_not_exported_as_a_second_entry_point() -> None:
    """Harness 根包和 Agent 包都只对外提供 Agent 对象。"""

    for path in (PACKAGE_ROOT / "__init__.py", PACKAGE_ROOT / "agent/__init__.py"):
        tree = ast.parse(path.read_text("utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "agent_loop" not in names | imported


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
