from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SkillSource(StrEnum):
    """Skill 的来源层级；工作区层优先级最高。"""

    BUILTIN = "builtin"
    USER = "user"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """可复用工作方法的稳定表示。"""

    name: str
    description: str
    instructions: str
    source: SkillSource
    version: str = "1.0.0"


class Skills:
    """从 builtin、用户与工作区三层目录发现 SKILL.md。"""

    def __init__(self, workspace: Path, user_root: Path | None = None) -> None:
        self._workspace_root = workspace / ".camel" / "skills"
        self._user_root = user_root or Path.home() / ".camel" / "skills"
        self._evolution_root = workspace / ".camel" / "skill-evolution"

    async def discover(self) -> tuple[SkillDefinition, ...]:
        return await asyncio.to_thread(self._discover_sync)

    async def get(self, name: str) -> SkillDefinition | None:
        return next((skill for skill in await self.discover() if skill.name == name), None)

    async def save(
        self, name: str, description: str, instructions: str, source: SkillSource
    ) -> SkillDefinition:
        if not _SAFE_NAME.fullmatch(name):
            raise ValueError("Skill 名称只能使用小写字母、数字、下划线和连字符")
        if source not in {SkillSource.USER, SkillSource.WORKSPACE}:
            raise ValueError("只能写入用户级或工作区级 Skill")
        root = self._workspace_root if source is SkillSource.WORKSPACE else self._user_root
        skill = SkillDefinition(name, description, instructions, source)
        await asyncio.to_thread(self._write_sync, root / name / "SKILL.md", skill)
        return skill

    async def record_usage(self, name: str, session_id: str) -> None:
        await asyncio.to_thread(
            self._append_audit,
            {"action": "used", "name": name, "session_id": session_id},
        )

    async def evolve(self, name: str, lesson: str, rationale: str) -> SkillDefinition:
        current = await self.get(name)
        if current is None:
            raise ValueError(f"Skill 不存在：{name}")
        version = self._bump_patch(current.version)
        addition = f"\n\n## 演化补充\n\n- {lesson.strip()}"
        if rationale.strip():
            addition += f"\n  - 原因：{rationale.strip()}"
        evolved = SkillDefinition(
            current.name,
            current.description,
            current.instructions.rstrip() + addition,
            SkillSource.WORKSPACE,
            version,
        )
        await asyncio.to_thread(self._write_sync, self._workspace_root / name / "SKILL.md", evolved)
        return evolved

    async def rollback(self, name: str) -> SkillDefinition:
        if not _SAFE_NAME.fullmatch(name):
            raise ValueError("Skill 名称包含不安全字符")
        return await asyncio.to_thread(self._rollback_sync, name)

    def _discover_sync(self) -> tuple[SkillDefinition, ...]:
        # 后加载层覆盖同名 Skill，确保项目规则具有最高优先级。
        found: dict[str, SkillDefinition] = {}
        for root, source in (
            (self._user_root, SkillSource.USER),
            (self._workspace_root, SkillSource.WORKSPACE),
        ):
            if not root.exists():
                continue
            for path in sorted(root.glob("*/SKILL.md")):
                skill = self._parse(path, source)
                found[skill.name] = skill
        return tuple(found.values())

    @staticmethod
    def _parse(path: Path, source: SkillSource) -> SkillDefinition:
        text = path.read_text("utf-8")
        metadata: dict[str, str] = {}
        body = text
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end >= 0:
                for line in text[4:end].splitlines():
                    key, separator, value = line.partition(":")
                    if separator:
                        metadata[key.strip()] = value.strip().strip('"')
                body = text[end + 5 :].strip()
        return SkillDefinition(
            name=metadata.get("name", path.parent.name),
            description=metadata.get("description", ""),
            instructions=body,
            source=source,
            version=metadata.get("version", "1.0.0"),
        )

    def _write_sync(self, path: Path, skill: SkillDefinition) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        previous_version = ""
        if path.exists():
            previous = self._parse(path, skill.source)
            previous_version = previous.version
            history_dir = self._evolution_root / "history" / skill.name
            history_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            snapshot = history_dir / f"{stamp}-{uuid4().hex[:8]}.md"
            snapshot.write_text(path.read_text("utf-8"), "utf-8")
        content = (
            "---\n"
            f"name: {skill.name}\n"
            f"description: {skill.description}\n"
            f"version: {skill.version}\n"
            "---\n\n"
            f"{skill.instructions.rstrip()}\n"
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, "utf-8")
        temporary.replace(path)
        self._append_audit(
            {
                "action": "created" if not previous_version else "evolved",
                "name": skill.name,
                "previous_version": previous_version,
                "version": skill.version,
            }
        )

    def _rollback_sync(self, name: str) -> SkillDefinition:
        history_dir = self._evolution_root / "history" / name
        snapshots = sorted(history_dir.glob("*.md"), reverse=True)
        if not snapshots:
            raise ValueError(f"Skill 没有可回滚版本：{name}")
        restored = self._parse(snapshots[0], SkillSource.WORKSPACE)
        self._write_sync(self._workspace_root / name / "SKILL.md", restored)
        self._append_audit({"action": "rolled_back", "name": name, "version": restored.version})
        return restored

    def _append_audit(self, row: dict[str, object]) -> None:
        path = self._evolution_root / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "event_id": uuid4().hex,
            "occurred_at": datetime.now(UTC).isoformat(),
            **row,
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _bump_patch(version: str) -> str:
        parts = version.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            return "1.0.1"
        return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
