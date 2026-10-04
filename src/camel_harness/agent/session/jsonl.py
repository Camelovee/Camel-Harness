from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from camel_harness.llm import Message

from .codec import message_from_dict, message_to_dict
from .log import SessionLog, SessionLogEvent, SurfaceOperation
from .metadata import SessionMetadata

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_VERSION = 2


class JsonlSessionStorage:
    """每个会话一份追加日志；按记录类型重建元数据、原始消息和上下文。"""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._locks: dict[str, asyncio.Lock] = {}

    async def create(self, metadata: SessionMetadata) -> None:
        path = self._path(metadata.session_id)
        async with self._lock(metadata.session_id):
            await asyncio.to_thread(self._create_sync, path, metadata)

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
        rows = await self._rows(session_id)
        return self._project_metadata(rows) if rows else None

    async def list_metadata(self) -> tuple[SessionMetadata, ...]:
        if not self._root.exists():
            return ()
        paths = [
            path
            for path in self._root.glob("*.jsonl")
            if _SAFE_ID.fullmatch(path.stem) and path.is_file() and not path.is_symlink()
        ]
        records = await asyncio.to_thread(
            lambda: [self._project_metadata(self._read_rows_sync(path)) for path in paths]
        )
        return tuple(sorted(records, key=lambda item: item.updated_at, reverse=True))

    async def update_metadata(self, metadata: SessionMetadata) -> None:
        async with self._lock(metadata.session_id):
            await asyncio.to_thread(
                self._append_sync,
                self._path(metadata.session_id),
                {
                    "type": "session_info",
                    "timestamp": metadata.updated_at,
                    "name": metadata.name,
                    "cwd": metadata.workspace,
                    "modelId": metadata.model_id,
                    "archived": metadata.archived,
                },
            )

    async def delete(self, session_id: str) -> bool:
        async with self._lock(session_id):
            path = self._path(session_id)
            if not path.exists():
                return False
            await asyncio.to_thread(path.unlink)
            return True

    async def _rows(self, session_id: str) -> list[dict[str, Any]]:
        async with self._lock(session_id):
            return await asyncio.to_thread(self._read_rows_sync, self._path(session_id))

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.fullmatch(session_id):
            raise ValueError("session_id 只能包含字母、数字、下划线和连字符")
        path = self._root / f"{session_id}.jsonl"
        if path.is_symlink():
            raise ValueError("会话文件不能是符号链接")
        return path

    @staticmethod
    def _create_sync(path: Path, metadata: SessionMetadata) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
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
        with path.open("x", encoding="utf-8") as stream:
            _write_row(stream, row)

    @staticmethod
    def _append_sync(path: Path, row: dict[str, Any]) -> None:
        if not path.exists():
            raise KeyError(f"会话不存在：{path.stem}")
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
            raise KeyError(f"会话不存在：{path.stem}")
        with path.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                rows = JsonlSessionStorage._read_rows_sync(path)
                events = JsonlSessionStorage._project_context(path.stem, rows)
                if event.seq != len(events):
                    raise ValueError("会话上下文已被并发修改，请重新加载后再压缩")
                assert event.replace_start is not None
                assert event.replace_end is not None
                SessionLog(path.stem, events).replace_context(
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
    def _read_rows_sync(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as stream:
            for number, line in enumerate(stream, start=1):
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise TypeError("记录必须是 JSON 对象")
                    if number == 1:
                        if row.get("type") != "session" or row.get("version") != _VERSION:
                            raise ValueError("会话头格式不受支持")
                        if row.get("id") != path.stem:
                            raise ValueError("会话头 ID 与文件名不一致")
                    elif row.get("type") not in {"message", "compaction", "session_info"}:
                        raise ValueError("未知会话记录类型")
                    rows.append(row)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(f"会话日志损坏：{path}:{number}") from exc
        if not rows:
            raise ValueError(f"会话日志为空：{path}")
        return rows

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
    def _project_metadata(rows: list[dict[str, Any]]) -> SessionMetadata:
        header = rows[0]
        name = str(header["name"])
        workspace = str(header["cwd"])
        model_id = header.get("modelId")
        archived = bool(header.get("archived", False))
        updated_at = str(header["timestamp"])
        message_count = 0
        last_message_type: str | None = None
        for row in rows[1:]:
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
