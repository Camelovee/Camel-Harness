from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from camel_harness.llm import Message

from .codec import message_from_dict, message_to_dict
from .log import SessionLog, SessionLogEvent, SurfaceOperation
from .metadata import SessionMetadata

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_VERSION = 2
logger = logging.getLogger(__name__)


class JsonlSessionStorage:
    """每个会话目录一份追加日志；子会话位于父目录的 subagents 下。"""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._locks: dict[str, asyncio.Lock] = {}
        self._locations: dict[str, Path] = {}

    def directory(self, session_id: str) -> Path:
        """定位已存在会话，关联文件复用日志的真实目录。"""
        path = self._path(session_id)
        if not path.is_file():
            raise KeyError(f"会话不存在：{session_id}")
        return path.parent

    async def create(self, metadata: SessionMetadata) -> None:
        async with self._lock(metadata.session_id):
            path = self._new_path(metadata)
            await asyncio.to_thread(self._create_sync, path, metadata)
            self._locations[metadata.session_id] = path

    async def append_message(self, session_id: str, message: Message) -> None:
        async with self._lock(session_id):
            await asyncio.to_thread(
                self._append_sync,
                self._path(session_id),
                {"type": "message", "timestamp": _now(), "message": message_to_dict(message)},
            )

    async def append_context_event(self, session_id: str, event: SessionLogEvent) -> None:
        if event.operation is not SurfaceOperation.REPLACE:
            raise ValueError("会话存储只接受压缩替换事件")
        async with self._lock(session_id):
            await asyncio.to_thread(self._append_compaction_sync, self._path(session_id), event)

    async def messages(self, session_id: str) -> tuple[Message, ...]:
        rows = await self._rows(session_id)
        return tuple(
            message_from_dict(row["message"]) for row in rows[1:] if row["type"] == "message"
        )

    async def context_events(self, session_id: str) -> tuple[SessionLogEvent, ...]:
        return self._project_context(session_id, await self._rows(session_id))

    async def get_metadata(self, session_id: str) -> SessionMetadata | None:
        async with self._lock(session_id):
            return await asyncio.to_thread(self._read_metadata_sync, self._path(session_id))

    async def list_metadata(self) -> tuple[SessionMetadata, ...]:
        if not self._root.exists():
            return ()
        paths = list(self._session_paths())
        records = await asyncio.to_thread(self._list_metadata_sync, paths)
        return tuple(sorted(records, key=lambda item: item.updated_at, reverse=True))

    async def update_metadata(
        self,
        session_id: str,
        *,
        name: str | None = None,
        workspace: str | None = None,
        archived: bool | None = None,
        model_id: str | None = None,
    ) -> SessionMetadata:
        async with self._lock(session_id):
            return await asyncio.to_thread(
                self._update_metadata_sync,
                self._path(session_id),
                name=name,
                workspace=workspace,
                archived=archived,
                model_id=model_id,
            )

    async def delete(self, session_id: str) -> bool:
        async with self._lock(session_id):
            path = self._path(session_id)
            if not path.exists():
                return False
            await asyncio.to_thread(self._delete_sync, path)
            self._locations.pop(session_id, None)
            return True

    async def _rows(self, session_id: str) -> list[dict[str, Any]]:
        async with self._lock(session_id):
            return await asyncio.to_thread(self._read_rows_sync, self._path(session_id))

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.fullmatch(session_id):
            raise ValueError("session_id 只能包含字母、数字、下划线和连字符")
        cached = self._locations.get(session_id)
        if cached is not None:
            self._assert_no_symlink(cached)
            if cached.exists():
                return cached
            self._locations.pop(session_id, None)
        direct = self._root / session_id / "session.jsonl"
        self._assert_no_symlink(direct)
        if direct.exists():
            self._locations[session_id] = direct
            return direct
        for path in self._session_paths():
            if path.parent.name == session_id:
                self._locations[session_id] = path
                return path
        return direct

    def _new_path(self, metadata: SessionMetadata) -> Path:
        existing = self._path(metadata.session_id)
        if existing.exists():
            raise ValueError(f"会话已存在：{metadata.session_id}")
        if metadata.parent_session_id is None:
            return existing
        parent = self._path(metadata.parent_session_id)
        if not parent.exists():
            raise ValueError(f"父会话不存在：{metadata.parent_session_id}")
        path = parent.parent / "subagents" / metadata.session_id / "session.jsonl"
        self._assert_no_symlink(path)
        return path

    def _session_paths(self) -> Iterator[Path]:
        if not self._root.is_dir():
            return
        for path in self._root.rglob("session.jsonl"):
            parts = path.relative_to(self._root).parts
            if len(parts) < 2 or len(parts) % 2 or parts[-1] != "session.jsonl":
                continue
            if any(parts[index] != "subagents" for index in range(1, len(parts) - 1, 2)):
                continue
            if any(not _SAFE_ID.fullmatch(parts[index]) for index in range(0, len(parts) - 1, 2)):
                continue
            if path.is_file() and not self._has_symlink(path):
                yield path

    def _has_symlink(self, path: Path) -> bool:
        current = path
        while current != self._root:
            if current.is_symlink():
                return True
            current = current.parent
        return False

    def _assert_no_symlink(self, path: Path) -> None:
        if self._has_symlink(path):
            raise ValueError("会话目录或文件不能是符号链接")

    @staticmethod
    def _create_sync(path: Path, metadata: SessionMetadata) -> None:
        path.parent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.mkdir(mode=0o700)
        if path.exists():
            raise ValueError(f"会话已存在：{metadata.session_id}")
        row = {
            "type": "session",
            "version": _VERSION,
            "id": metadata.session_id,
            "timestamp": metadata.created_at,
            "cwd": metadata.workspace,
            "name": metadata.name,
            "modelId": metadata.model_id,
            "archived": metadata.archived,
            "parentSessionId": metadata.parent_session_id,
            "parentToolCallId": metadata.parent_tool_call_id,
        }
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _write_row(stream, row)

    @staticmethod
    def _delete_sync(path: Path) -> None:
        results = path.parent / "tool-results"
        if results.is_dir() and not results.is_symlink():
            for result in results.glob("*.txt"):
                if result.is_file() or result.is_symlink():
                    result.unlink()
            try:
                results.rmdir()
            except OSError:
                # 不删除未知文件或目录。
                pass
        path.unlink()
        try:
            path.parent.rmdir()
            if path.parent.parent.name == "subagents":
                path.parent.parent.rmdir()
        except OSError:
            # 只删除会话自身的日志和空目录，保留未知文件与仍在使用的子目录。
            pass

    @staticmethod
    def _append_sync(path: Path, row: dict[str, Any]) -> None:
        if not path.exists():
            raise KeyError(f"会话不存在：{path.parent.name}")
        with path.open("rb+") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                header = json.loads(stream.readline())
                if (
                    not isinstance(header, dict)
                    or header.get("type") != "session"
                    or header.get("version") != _VERSION
                ):
                    raise ValueError(f"会话头格式不受支持：{path}")
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    raise ValueError(f"会话日志为空：{path}")
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    raise ValueError(f"会话日志尾部不完整：{path}")
                stream.seek(0, os.SEEK_END)
                stream.write(
                    (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                        "utf-8"
                    )
                )
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _append_compaction_sync(path: Path, event: SessionLogEvent) -> None:
        if not path.exists():
            raise KeyError(f"会话不存在：{path.parent.name}")
        with path.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                stream.seek(0)
                rows = list(JsonlSessionStorage._iter_rows(stream, path))
                events = JsonlSessionStorage._project_context(path.parent.name, rows)
                if event.seq != len(events):
                    raise ValueError("会话上下文已被并发修改，请重新加载后再压缩")
                assert event.replace_start is not None
                assert event.replace_end is not None
                SessionLog(path.parent.name, events).replace_context(
                    event.replace_start, event.replace_end, event.message
                )
                stream.seek(0, os.SEEK_END)
                _write_row(
                    stream,
                    {
                        "type": "compaction",
                        "timestamp": _now(),
                        "start": event.replace_start,
                        "end": event.replace_end,
                        "summary": message_to_dict(event.message),
                    },
                )
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _update_metadata_sync(
        path: Path,
        *,
        name: str | None,
        workspace: str | None,
        archived: bool | None,
        model_id: str | None,
    ) -> SessionMetadata:
        if not path.exists():
            raise KeyError(f"会话不存在：{path.parent.name}")
        with path.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                current = JsonlSessionStorage._project_metadata(
                    JsonlSessionStorage._iter_rows(stream, path)
                )
                updated = current.update(
                    name=name, workspace=workspace, archived=archived, model_id=model_id
                )
                stream.seek(0, os.SEEK_END)
                _write_row(
                    stream,
                    {
                        "type": "session_info",
                        "timestamp": updated.updated_at,
                        "name": updated.name,
                        "cwd": updated.workspace,
                        "modelId": updated.model_id,
                        "archived": updated.archived,
                    },
                )
                return updated
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read_rows_sync(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            try:
                return list(JsonlSessionStorage._iter_rows(stream, path))
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read_metadata_sync(path: Path) -> SessionMetadata | None:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            try:
                return JsonlSessionStorage._project_metadata(
                    JsonlSessionStorage._iter_rows(stream, path)
                )
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _list_metadata_sync(paths: list[Path]) -> list[SessionMetadata]:
        """坏会话不应阻断其他会话的列表读取。"""

        records: list[SessionMetadata] = []
        for path in paths:
            try:
                metadata = JsonlSessionStorage._read_metadata_sync(path)
                if metadata is not None:
                    records.append(metadata)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logger.warning("跳过无法读取的会话日志 %s：%s", path, exc)
        return records

    @staticmethod
    def _iter_rows(stream: TextIO, path: Path) -> Iterator[dict[str, Any]]:
        """逐行校验日志，供完整重放和常量内存元数据投影复用。"""

        found = False
        for number, line in enumerate(stream, start=1):
            try:
                if not line.endswith("\n"):
                    raise ValueError("会话日志尾部不完整")
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise TypeError("记录必须是 JSON 对象")
                if number == 1:
                    if row.get("type") != "session" or row.get("version") != _VERSION:
                        raise ValueError("会话头格式不受支持")
                    if row.get("id") != path.parent.name:
                        raise ValueError("会话头 ID 与目录名不一致")
                    parent_id = (
                        path.parent.parent.parent.name
                        if path.parent.parent.name == "subagents"
                        else None
                    )
                    if row.get("parentSessionId") != parent_id:
                        raise ValueError("会话头父 ID 与目录层级不一致")
                elif row.get("type") not in {"message", "compaction", "session_info"}:
                    raise ValueError("未知会话记录类型")
                found = True
                yield row
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"会话日志损坏：{path}:{number}：{exc}") from exc
        if not found:
            raise ValueError(f"会话日志为空：{path}")

    @staticmethod
    def _project_context(
        session_id: str, rows: list[dict[str, Any]]
    ) -> tuple[SessionLogEvent, ...]:
        log = SessionLog(session_id)
        message_index = 0
        for row in rows[1:]:
            if row["type"] == "message":
                log.append_message(message_index, message_from_dict(row["message"]))
                message_index += 1
            elif row["type"] == "compaction":
                log.replace_context(
                    int(row["start"]), int(row["end"]), message_from_dict(row["summary"])
                )
        return log.events

    @staticmethod
    def _project_metadata(rows: Iterable[dict[str, Any]]) -> SessionMetadata:
        iterator = iter(rows)
        header = next(iterator)
        name = str(header["name"])
        workspace = str(header["cwd"])
        model_id = header.get("modelId")
        archived = bool(header.get("archived", False))
        updated_at = str(header["timestamp"])
        message_count = 0
        last_message_type: str | None = None
        for row in iterator:
            if row["type"] == "message":
                message_count += 1
                last_message_type = str(row["message"]["role"])
                updated_at = str(row["timestamp"])
            elif row["type"] == "session_info":
                name = str(row["name"])
                workspace = str(row["cwd"])
                model_id = row.get("modelId")
                archived = bool(row["archived"])
                updated_at = str(row["timestamp"])
        return SessionMetadata(
            session_id=str(header["id"]),
            name=name,
            workspace=workspace,
            created_at=str(header["timestamp"]),
            updated_at=updated_at,
            message_count=message_count,
            last_message_type=last_message_type,
            model_id=str(model_id) if model_id is not None else None,
            archived=archived,
            parent_session_id=header.get("parentSessionId"),
            parent_tool_call_id=header.get("parentToolCallId"),
            schema_version=_VERSION,
        )


def _write_row(stream: TextIO, row: dict[str, Any]) -> None:
    stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def _now() -> str:
    return datetime.now(UTC).isoformat()
