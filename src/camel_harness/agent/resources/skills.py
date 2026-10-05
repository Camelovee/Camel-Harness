from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_FRONTMATTER_LINES = 100
logger = logging.getLogger(__name__)


class SkillSource(StrEnum):
    """Skill 来源按内置、项目、用户的优先级排列。"""

    BUILTIN = "builtin"
    PROJECT = "project"
    USER = "user"


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """第一层只暴露发现所需的元数据，不加载正文。"""

    name: str
    description: str
    source: SkillSource
    path: Path


class Skills:
    """发现 Skill，并按需加载正文和支持文件。"""

    def __init__(self, workspace: Path, user_root: Path | None = None) -> None:
        self._builtin_root = Path(__file__).parent / "builtin_skills"
        self._project_root = workspace / ".camel" / "skills"
        self._user_root = user_root or Path.home() / ".camel" / "skills"

    async def discover(self) -> tuple[SkillDefinition, ...]:
        """仅读取 SKILL.md frontmatter，供系统提示展示名称与描述。"""

        return await asyncio.to_thread(self._discover_sync)

    async def load(self, name: str) -> str:
        """第二层在 Skill 被选中时读取 SKILL.md 正文。"""

        return await asyncio.to_thread(self._load_sync, name)

    async def read_resource(self, name: str, relative_path: str) -> str:
        """第三层在正文引用支持文件时按需读取 UTF-8 文本。"""

        return await asyncio.to_thread(self._read_resource_sync, name, relative_path)

    def _discover_sync(self) -> tuple[SkillDefinition, ...]:
        found: dict[str, SkillDefinition] = {}
        for root, source in (
            (self._builtin_root, SkillSource.BUILTIN),
            (self._project_root, SkillSource.PROJECT),
            (self._user_root, SkillSource.USER),
        ):
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/SKILL.md")):
                if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
                    continue
                try:
                    metadata = self._frontmatter(path)
                except (OSError, UnicodeError) as exc:
                    logger.warning("跳过无法读取的 Skill %s：%s", path, exc)
                    continue
                if metadata is None:
                    continue
                name = metadata.get("name", path.parent.name)
                if not _SAFE_NAME.fullmatch(name):
                    continue
                found.setdefault(
                    name, SkillDefinition(name, metadata.get("description", ""), source, path)
                )
        return tuple(found.values())

    def _resolve(self, name: str) -> SkillDefinition:
        if not _SAFE_NAME.fullmatch(name):
            raise ValueError("Skill 名称包含不安全字符")
        skill = next((item for item in self._discover_sync() if item.name == name), None)
        if skill is None:
            raise ValueError(f"Skill 不存在：{name}")
        return skill

    def _load_sync(self, name: str) -> str:
        path = self._resolve(name).path
        text = path.read_text("utf-8")
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise ValueError(f"Skill 缺少 frontmatter：{name}")
        for index, line in enumerate(lines[1 : _MAX_FRONTMATTER_LINES + 1], start=1):
            if line.strip() == "---":
                return "".join(lines[index + 1 :]).strip()
        raise ValueError(f"Skill frontmatter 未结束：{name}")

    def _read_resource_sync(self, name: str, relative_path: str) -> str:
        base = self._resolve(name).path.parent.resolve()
        if not relative_path or Path(relative_path).is_absolute():
            raise ValueError("Skill 支持文件必须使用相对路径")
        path = (base / relative_path).resolve()
        if path == base or not path.is_relative_to(base) or not path.is_file():
            raise ValueError("Skill 支持文件不存在或超出 Skill 目录")
        if path.name == "SKILL.md":
            raise ValueError("请通过加载 Skill 正文读取 SKILL.md")
        return path.read_text("utf-8")

    @staticmethod
    def _frontmatter(path: Path) -> dict[str, str] | None:
        """只读取 YAML 头部的名称和描述，正文绝不进入发现阶段。"""

        with path.open("rb") as stream:
            if stream.readline().decode("utf-8").strip() != "---":
                return None
            metadata: dict[str, str] = {}
            multiline_key: str | None = None
            multiline_values: list[str] = []
            for _ in range(_MAX_FRONTMATTER_LINES):
                raw_line = stream.readline()
                if not raw_line:
                    return None
                line = raw_line.decode("utf-8")
                if line.strip() == "---":
                    if multiline_key is not None:
                        metadata[multiline_key] = " ".join(multiline_values)
                    return metadata
                if multiline_key is not None:
                    if line.startswith((" ", "\t")):
                        multiline_values.append(line.strip())
                        continue
                    metadata[multiline_key] = " ".join(multiline_values)
                    multiline_key = None
                    multiline_values = []
                key, separator, value = line.partition(":")
                if not separator or key not in {"name", "description"}:
                    continue
                value = value.strip()
                if value in {"|", ">", "|-", ">-"}:
                    multiline_key = key
                else:
                    metadata[key] = value.strip("\"'")
        return None
