from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src/camel_harness"


def test_core_uses_pi_agent_style_package_root() -> None:
    """Agent 核心位于包根，产品能力进入 Harness 子包。"""

    core_files = {
        "agent.py",
        "agent_loop.py",
        "events.py",
        "options.py",
        "run.py",
        "subagent.py",
    }
    assert all((PACKAGE_ROOT / name).is_file() for name in core_files)
    assert not (PACKAGE_ROOT / "agent").exists()
    assert all((PACKAGE_ROOT / name).is_dir() for name in {"harness", "backend", "cli"})


def test_feature_modules_do_not_restore_ddd_layers() -> None:
    """功能模块内不得机械创建 DDD 分层目录。"""

    forbidden = {"models", "services", "ports", "domain", "infra", "application"}
    violations = [
        path for path in PACKAGE_ROOT.rglob("*") if path.is_dir() and path.name in forbidden
    ]
    assert not violations


def test_tool_contract_lives_in_agent_types() -> None:
    """工具契约属于可独立复用的 Agent 包。"""

    tool_tree = ast.parse((PACKAGE_ROOT / "types/tool.py").read_text("utf-8"))
    names = {node.name for node in tool_tree.body if isinstance(node, ast.ClassDef)}
    assert {"Tool", "ToolCall", "ToolContext", "ToolDefinition", "ToolResult"} <= names


def test_agent_does_not_depend_on_application_packages() -> None:
    """Agent 核心不能反向依赖 Harness 或入口层。"""

    forbidden = {"camel_harness.harness", "camel_harness.backend", "camel_harness.cli"}
    core_paths = [
        *(
            PACKAGE_ROOT / name
            for name in (
                "agent.py",
                "agent_loop.py",
                "events.py",
                "options.py",
                "run.py",
                "subagent.py",
            )
        ),
        PACKAGE_ROOT / "llm",
        PACKAGE_ROOT / "tools",
        PACKAGE_ROOT / "types",
    ]
    assert not [item for path in core_paths for item in _forbidden_imports(path, forbidden)]


def test_harness_does_not_depend_on_entry_packages() -> None:
    """产品编排不能依赖 HTTP 或 CLI 实现。"""

    assert not _forbidden_imports(
        PACKAGE_ROOT / "harness", {"camel_harness.backend", "camel_harness.cli"}
    )


def test_runtime_is_the_only_full_composition_module() -> None:
    """完整对象图只在 Harness 的 runtime 中装配。"""

    assert (PACKAGE_ROOT / "harness/runtime.py").is_file()
    assert not (PACKAGE_ROOT / "runtime.py").exists()
    assert not (PACKAGE_ROOT / "harness/bootstrap").exists()


def test_types_only_depend_on_standard_library() -> None:
    """稳定公共类型不得依赖项目运行模块或第三方库。"""

    violations: list[str] = []
    for path in (PACKAGE_ROOT / "types").glob("*.py"):
        for module in _absolute_imports(path):
            if module.startswith("camel_harness."):
                violations.append(f"{path.name}: {module}")
    assert not violations


def _forbidden_imports(root: Path, forbidden: set[str]) -> list[str]:
    violations: list[str] = []
    paths = [root] if root.is_file() else root.rglob("*.py")
    for path in paths:
        for module in _absolute_imports(path):
            if any(module == item or module.startswith(f"{item}.") for item in forbidden):
                violations.append(f"{path.relative_to(PACKAGE_ROOT)}: {module}")
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
