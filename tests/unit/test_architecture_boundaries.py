from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "backend"


def test_backend_uses_feature_modules_without_ddd_layers() -> None:
    """后端使用功能模块，不恢复 application/domain/infra 分层。"""

    expected = {
        "agent",
        "api",
        "compaction",
        "llm",
        "resources",
        "session",
        "tools",
        "types",
    }
    packages = {
        path.parent.name
        for path in PACKAGE_ROOT.glob("*/__init__.py")
        if path.parent != PACKAGE_ROOT
    }
    assert expected <= packages
    assert not {"adapter", "bootstrap", "core", "domain", "infra"} & packages


def test_feature_modules_do_not_restore_layer_folders() -> None:
    """功能模块内不得机械创建 models、services 和 ports 目录。"""

    forbidden = {"models", "services", "ports"}
    violations = [
        path for path in PACKAGE_ROOT.rglob("*") if path.is_dir() and path.name in forbidden
    ]
    assert not violations


def test_tool_contract_lives_in_types() -> None:
    """Tool ABC 属于公共类型，工具模块只包含实现和执行能力。"""

    tool_tree = ast.parse((PACKAGE_ROOT / "types/tool.py").read_text("utf-8"))
    names = {node.name for node in tool_tree.body if isinstance(node, ast.ClassDef)}
    assert {"Tool", "ToolCall", "ToolContext", "ToolDefinition", "ToolResult"} <= names
    assert not (PACKAGE_ROOT / "tools/tool.py").exists()


def test_agent_loop_does_not_own_persistence_or_resources() -> None:
    """低层 Agent Loop 只依赖模型、工具和公共类型。"""

    imports = _absolute_imports(PACKAGE_ROOT / "agent/agent_loop.py")
    forbidden = {
        "backend.api",
        "backend.compaction",
        "backend.harness",
        "backend.resources",
        "backend.session",
    }
    assert not {
        module
        for module in imports
        if any(module == item or module.startswith(f"{item}.") for item in forbidden)
    }


def test_runtime_is_the_only_composition_module() -> None:
    """运行时装配集中在单一文件，不恢复分区 wiring。"""

    assert (PACKAGE_ROOT / "runtime.py").is_file()
    assert not (PACKAGE_ROOT / "runtime_parts").exists()
    assert not (PACKAGE_ROOT / "bootstrap").exists()


def test_types_do_not_depend_on_runtime_features() -> None:
    """公共类型不得反向依赖 Agent、Provider、存储或 HTTP。"""

    violations: list[str] = []
    for path in (PACKAGE_ROOT / "types").glob("*.py"):
        for module in _absolute_imports(path):
            if module.startswith("backend.") and not module.startswith("backend.types"):
                violations.append(f"{path.name}: {module}")
    assert not violations


def _absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules
