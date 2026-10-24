"""把旧 JSON 记忆条目一次性转换为 MEMORY.md / USER.md；默认只预览。"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from camel_harness.agent.config import MemorySettings
from camel_harness.agent.resources import MemoryStore

MemoryTarget = Literal["memory", "user"]
_ENTRY_SEPARATOR = "\n§\n"
_FILENAMES: dict[MemoryTarget, str] = {"memory": "MEMORY.md", "user": "USER.md"}


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """一个目标的待写入条目与未容纳条目。"""

    target: MemoryTarget
    path: Path
    entries: tuple[str, ...]
    skipped: tuple[str, ...]
    used_chars: int


def plan_migration(
    workspace: Path,
    user_root: Path,
    *,
    target: MemoryTarget | None = None,
    settings: MemorySettings | None = None,
) -> tuple[MigrationPlan, ...]:
    """读取旧 JSON 条目并生成计划；不写文件，不删除旧数据。"""

    effective_settings = settings or MemorySettings()
    resolved_workspace, user_directory = _resolved_roots(workspace, user_root)
    memory_root = resolved_workspace / ".camel" / "memory"
    legacy_memory = _legacy_files(memory_root)
    legacy_user = _legacy_files(user_directory)

    if memory_root == user_directory:
        if not legacy_memory:
            return ()
        if target is None:
            raise ValueError(
                "工作区与用户记忆目录相同，旧条目无法判断归属；"
                "请用 --target memory 或 --target user 指定"
            )
        roots: tuple[tuple[MemoryTarget, Path, tuple[Path, ...]], ...] = (
            (target, memory_root, legacy_memory),
        )
    else:
        roots = (
            ("memory", memory_root, legacy_memory),
            ("user", user_directory, legacy_user),
        )
        if target is not None:
            roots = tuple(item for item in roots if item[0] == target)

    plans: list[MigrationPlan] = []
    for selected_target, root, files in roots:
        if not files:
            continue
        entries = tuple(_convert(path) for path in files)
        limit = (
            effective_settings.memory_char_limit
            if selected_target == "memory"
            else effective_settings.user_char_limit
        )
        selected, skipped, used_chars = _select_within_limit(entries, limit)
        plans.append(
            MigrationPlan(
                target=selected_target,
                path=root / _FILENAMES[selected_target],
                entries=selected,
                skipped=skipped,
                used_chars=used_chars,
            )
        )
    return tuple(plans)


async def apply_migration(
    workspace: Path,
    user_root: Path,
    plans: tuple[MigrationPlan, ...],
    *,
    force: bool,
    settings: MemorySettings | None = None,
) -> None:
    """按计划整文件写入；已存在目标时默认拒绝覆盖。"""

    effective_settings = settings or MemorySettings()
    resolved_workspace, resolved_user_root = _resolved_roots(workspace, user_root)
    memory_store = MemoryStore(resolved_workspace, effective_settings, user_root=resolved_user_root)
    for plan in plans:
        if not plan.entries:
            continue
        if plan.path.exists() and not force:
            raise ValueError(f"目标记忆已存在，未覆盖：{plan.path}；使用 --force 覆盖")
        await memory_store.import_entries(plan.target, plan.entries)


def _resolved_roots(workspace: Path, user_root: Path) -> tuple[Path, Path]:
    """命令行路径允许包含符号链接，先规范化为真实路径。"""

    workspace_path = workspace.expanduser().resolve()
    return workspace_path, user_root.expanduser().resolve()


def _legacy_files(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    files: list[Path] = []
    for path in sorted(root.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"旧记忆条目必须是普通文件：{path}")
        files.append(path)
    return tuple(files)


def _convert(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"旧记忆条目无法读取：{path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"旧记忆条目格式错误：{path}")
    name = data.get("name")
    description = data.get("description")
    content = data.get("content")
    if not all(isinstance(value, str) and value.strip() for value in (name, description, content)):
        raise ValueError(f"旧记忆条目字段不完整：{path}")
    return f"[{name.strip()}] {description.strip()}\n{content.strip()}"


def _select_within_limit(
    entries: tuple[str, ...], limit: int
) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    selected: list[str] = []
    skipped: list[str] = []
    used_chars = 0
    for entry in entries:
        addition = len(entry) + (len(_ENTRY_SEPARATOR) if selected else 0)
        if used_chars + addition > limit:
            skipped.append(entry.splitlines()[0])
            continue
        selected.append(entry)
        used_chars += addition
    return tuple(selected), tuple(skipped), used_chars


def _describe(plans: tuple[MigrationPlan, ...]) -> None:
    for plan in plans:
        print(f"{plan.target}: 写入 {len(plan.entries)} 条，{plan.used_chars} 字符 -> {plan.path}")
        if plan.skipped:
            print(f"{plan.target}: {len(plan.skipped)} 条超过字符上限，未迁移：")
            for entry in plan.skipped:
                print(f"  - {entry}")


def main() -> None:
    parser = argparse.ArgumentParser(description="把旧 JSON 记忆条目迁移为 MEMORY.md / USER.md")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--user-root", type=Path, default=Path.home() / ".camel" / "memory")
    parser.add_argument("--target", choices=("memory", "user"), default=None)
    parser.add_argument("--apply", action="store_true", help="实际写入；旧 JSON 保留")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的目标记忆")
    arguments = parser.parse_args()

    try:
        plans = plan_migration(
            arguments.workspace,
            arguments.user_root,
            target=arguments.target,
        )
    except ValueError as exc:
        print(f"迁移中止：{exc}")
        raise SystemExit(2) from exc
    if not plans:
        print("没有发现旧 JSON 记忆条目")
        return
    _describe(plans)
    if not arguments.apply:
        print("预览完成；使用 --apply 写入")
        return
    try:
        asyncio.run(
            apply_migration(
                arguments.workspace,
                arguments.user_root,
                plans,
                force=arguments.force,
            )
        )
    except ValueError as exc:
        print(f"迁移中止：{exc}")
        raise SystemExit(2) from exc
    if any(plan.skipped for plan in plans):
        print("部分条目因超过字符上限未迁移，旧 JSON 仍保留")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
