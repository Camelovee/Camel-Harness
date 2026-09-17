from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from .memory_types import MemoryEntry

_SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class MemoryStore:
    """以独立 JSON 文件保存项目级长期记忆。"""

    def __init__(self, workspace: Path) -> None:
        self._root = workspace / ".camel" / "memory"

    async def list(self) -> tuple[MemoryEntry, ...]:
        return await asyncio.to_thread(self._list_sync)

    async def get(self, name: str) -> MemoryEntry | None:
        self._validate_name(name)
        path = self._root / f"{name}.json"
        if not path.exists():
            return None
        return await asyncio.to_thread(self._read_sync, path)

    async def save(self, entry: MemoryEntry) -> None:
        self._validate_name(entry.name)
        await asyncio.to_thread(self._write_sync, self._root / f"{entry.name}.json", entry)

    async def delete(self, name: str) -> bool:
        self._validate_name(name)
        path = self._root / f"{name}.json"
        if not path.exists():
            return False
        await asyncio.to_thread(path.unlink)
        return True

    def _list_sync(self) -> tuple[MemoryEntry, ...]:
        if not self._root.exists():
            return ()
        return tuple(self._read_sync(path) for path in sorted(self._root.glob("*.json")))

    @staticmethod
    def _read_sync(path: Path) -> MemoryEntry:
        raw: dict[str, Any] = json.loads(path.read_text("utf-8"))
        return MemoryEntry(
            name=str(raw["name"]),
            description=str(raw.get("description", "")),
            content=str(raw.get("content", "")),
            kind=str(raw.get("kind", "project")),
        )

    @staticmethod
    def _write_sync(path: Path, entry: MemoryEntry) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "name": entry.name,
                    "description": entry.description,
                    "content": entry.content,
                    "kind": entry.kind,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            "utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _SAFE_NAME.fullmatch(name):
            raise ValueError("Memory 名称包含不安全字符")
