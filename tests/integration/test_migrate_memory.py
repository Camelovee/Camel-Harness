from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from camel_harness.agent.config import MemorySettings

_SCRIPT = Path(__file__).parents[2] / "scripts" / "migrate_memory.py"
_SPEC = importlib.util.spec_from_file_location("migrate_memory", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
migrate_memory = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = migrate_memory
_SPEC.loader.exec_module(migrate_memory)


def _write_legacy(
    root: Path,
    name: str,
    content: str = "使用中文注释",
    description: str = "编码规则",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.json").write_text(
        json.dumps(
            {"name": name, "description": description, "content": content},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_preview_then_apply_converts_and_keeps_legacy_files(tmp_path: Path) -> None:
    workspace, user_root = tmp_path / "workspace", tmp_path / "user"
    legacy_root = workspace / ".camel" / "memory"
    _write_legacy(legacy_root, "style")
    target = legacy_root / "MEMORY.md"

    plans = migrate_memory.plan_migration(workspace, user_root)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.target == "memory"
    assert plan.entries == ("[style] 编码规则\n使用中文注释",)
    assert plan.path == target
    assert not target.exists()

    asyncio.run(migrate_memory.apply_migration(workspace, user_root, plans, force=False))
    assert target.read_text(encoding="utf-8") == "[style] 编码规则\n使用中文注释"
    assert (legacy_root / "style.json").exists()


def test_existing_target_requires_force(tmp_path: Path) -> None:
    workspace, user_root = tmp_path / "workspace", tmp_path / "user"
    _write_legacy(workspace / ".camel" / "memory", "style")
    plans = migrate_memory.plan_migration(workspace, user_root)
    asyncio.run(migrate_memory.apply_migration(workspace, user_root, plans, force=False))

    with pytest.raises(ValueError, match="未覆盖"):
        asyncio.run(migrate_memory.apply_migration(workspace, user_root, plans, force=False))
    asyncio.run(migrate_memory.apply_migration(workspace, user_root, plans, force=True))


def test_over_limit_entries_are_reported_and_not_written(tmp_path: Path) -> None:
    workspace, user_root = tmp_path / "workspace", tmp_path / "user"
    legacy_root = workspace / ".camel" / "memory"
    _write_legacy(legacy_root, "large", content="长" * 30)

    plans = migrate_memory.plan_migration(
        workspace,
        user_root,
        settings=MemorySettings(memory_char_limit=10),
    )
    assert plans[0].entries == ()
    assert len(plans[0].skipped) == 1

    asyncio.run(
        migrate_memory.apply_migration(
            workspace,
            user_root,
            plans,
            force=False,
            settings=MemorySettings(memory_char_limit=10),
        )
    )
    assert not (legacy_root / "MEMORY.md").exists()
    assert (legacy_root / "large.json").exists()


def test_shared_root_requires_explicit_target(tmp_path: Path) -> None:
    user_root = tmp_path / ".camel" / "memory"
    _write_legacy(user_root, "style")

    with pytest.raises(ValueError, match="无法判断归属"):
        migrate_memory.plan_migration(tmp_path, user_root)

    plans = migrate_memory.plan_migration(tmp_path, user_root, target="user")
    assert plans[0].target == "user"
    assert plans[0].path == user_root / "USER.md"
    asyncio.run(migrate_memory.apply_migration(tmp_path, user_root, plans, force=False))
    assert (user_root / "USER.md").exists()


def test_symlinked_workspace_argument_is_resolved(tmp_path: Path) -> None:
    real_workspace = tmp_path / "real"
    real_workspace.mkdir()
    _write_legacy(real_workspace / ".camel" / "memory", "style")
    linked_workspace = tmp_path / "linked"
    linked_workspace.symlink_to(real_workspace, target_is_directory=True)

    plans = migrate_memory.plan_migration(linked_workspace, tmp_path / "user")

    assert len(plans) == 1
    assert plans[0].path == real_workspace / ".camel" / "memory" / "MEMORY.md"


def test_force_overwrites_unparsable_target(tmp_path: Path) -> None:
    workspace, user_root = tmp_path / "workspace", tmp_path / "user"
    legacy_root = workspace / ".camel" / "memory"
    _write_legacy(legacy_root, "style")
    target = legacy_root / "MEMORY.md"
    target.write_bytes(b"\xff\xfe")

    plans = migrate_memory.plan_migration(workspace, user_root)
    asyncio.run(migrate_memory.apply_migration(workspace, user_root, plans, force=True))

    assert target.read_text(encoding="utf-8") == "[style] 编码规则\n使用中文注释"


def test_invalid_legacy_entry_aborts(tmp_path: Path) -> None:
    legacy_root = tmp_path / "workspace" / ".camel" / "memory"
    legacy_root.mkdir(parents=True)
    (legacy_root / "broken.json").write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="无法读取"):
        migrate_memory.plan_migration(tmp_path / "workspace", tmp_path / "user")
