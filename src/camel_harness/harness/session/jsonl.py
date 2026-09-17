from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from camel_harness.types import (
    Message,
    MessageRole,
    message_from_dict,
    message_to_dict,
)

from .log import SessionLog, SessionLogEvent, SurfaceOperation
from .metadata import SessionMetadata

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class JsonlSessionStorage:
    """纯本地 JSONL 会话存储，每个 Session 使用独立目录。"""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._locks: dict[str, asyncio.Lock] = {}

    async def append_message(self, session_id: str, message: Message) -> None:
        path = self._message_path(session_id)
        context_path = self._context_path(session_id)
        metadata_path = self._metadata_path(session_id)
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            await asyncio.to_thread(
                self._append_message_sync,
                path,
                context_path,
                metadata_path,
                session_id,
                message,
            )

    async def messages(self, session_id: str) -> tuple[Message, ...]:
        path = self._message_path(session_id)
        if not path.exists():
            return ()
        rows = await asyncio.to_thread(path.read_text, "utf-8")
        messages: list[Message] = []
        for line_number, line in enumerate(rows.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                raw: dict[str, Any] = json.loads(line)
                messages.append(message_from_dict(raw))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"会话消息文件损坏：{path}:{line_number}") from exc
        return _merge_adjacent_tool_messages(messages)

    async def context_events(self, session_id: str) -> tuple[SessionLogEvent, ...]:
        """读取上下文日志，并补齐异常退出时尚未记录的尾部消息引用。"""

        message_path = self._message_path(session_id)
        context_path = self._context_path(session_id)
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            return await asyncio.to_thread(self._context_events_sync, message_path, context_path)

    async def append_context_event(self, session_id: str, event: SessionLogEvent) -> None:
        """把压缩摘要作为 Replace 事件追加到 context.jsonl。"""

        message_path = self._message_path(session_id)
        context_path = self._context_path(session_id)
        metadata_path = self._metadata_path(session_id)
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            if not metadata_path.exists():
                raise KeyError(f"会话不存在：{session_id}")
            return await asyncio.to_thread(
                self._append_context_event_sync,
                message_path,
                context_path,
                event,
            )

    async def create(self, metadata: SessionMetadata) -> None:
        metadata_path = self._metadata_path(metadata.session_id)
        message_path = self._message_path(metadata.session_id)
        context_path = self._context_path(metadata.session_id)
        lock = self._locks.setdefault(metadata.session_id, asyncio.Lock())
        async with lock:
            await asyncio.to_thread(
                self._create_metadata_sync,
                metadata_path,
                message_path,
                context_path,
                metadata,
            )

    async def get_metadata(self, session_id: str) -> SessionMetadata | None:
        path = self._metadata_path(session_id)
        if not path.exists():
            return None
        return await asyncio.to_thread(self._read_metadata_sync, path)

    async def list_metadata(self) -> tuple[SessionMetadata, ...]:
        return await asyncio.to_thread(self._list_metadata_sync)

    async def update_metadata(self, metadata: SessionMetadata) -> None:
        path = self._metadata_path(metadata.session_id)
        lock = self._locks.setdefault(metadata.session_id, asyncio.Lock())
        async with lock:
            if not path.exists():
                raise KeyError(f"会话不存在：{metadata.session_id}")
            await asyncio.to_thread(self._write_metadata_sync, path, metadata)

    async def delete(self, session_id: str) -> bool:
        message_path = self._message_path(session_id)
        context_path = self._context_path(session_id)
        metadata_path = self._metadata_path(session_id)
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            return await asyncio.to_thread(
                self._delete_sync, message_path, context_path, metadata_path
            )

    def _message_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "messages.jsonl"

    def _metadata_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "metadata.json"

    def _context_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "context.jsonl"

    def _session_dir(self, session_id: str) -> Path:
        if not _SAFE_ID.fullmatch(session_id):
            raise ValueError("session_id 只能包含字母、数字、下划线和连字符")
        path = self._root / session_id
        if path.is_symlink():
            raise ValueError("会话目录不能是符号链接")
        return path

    def _append_message_sync(
        self,
        path: Path,
        context_path: Path,
        metadata_path: Path,
        session_id: str,
        message: Message,
    ) -> None:
        if not metadata_path.exists():
            raise KeyError(f"会话不存在：{session_id}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                existing_messages = self._read_message_rows_sync(path)
                events = self._context_events_sync(path, context_path, existing_messages)
                message_index = len(existing_messages)
                stream.write(
                    json.dumps(message_to_dict(message), ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
                event = SessionLog(session_id, events).append_message(message_index, message)
                self._append_context_row_sync(
                    context_path,
                    {
                        "seq": event.seq,
                        "op": event.operation.value,
                        "messageIndex": message_index,
                    },
                )
                metadata = self._read_metadata_sync(metadata_path)
                self._write_metadata_sync(
                    metadata_path, metadata.record_messages(1, message.role.value)
                )
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _context_events_sync(
        self,
        message_path: Path,
        context_path: Path,
        messages: list[Message] | None = None,
    ) -> tuple[SessionLogEvent, ...]:
        raw_messages = (
            messages if messages is not None else self._read_message_rows_sync(message_path)
        )
        rows = self._read_context_rows_sync(context_path)
        referenced: set[int] = set()
        events: list[SessionLogEvent] = []
        for expected_seq, raw in enumerate(rows):
            try:
                seq = int(raw["seq"])
                if seq != expected_seq:
                    raise ValueError(f"上下文日志序号必须连续：期望 {expected_seq}，实际 {seq}")
                operation = SurfaceOperation(str(raw["op"]))
                if operation is SurfaceOperation.APPEND:
                    message_index = int(raw["messageIndex"])
                    if message_index != len(referenced) or not 0 <= message_index < len(
                        raw_messages
                    ):
                        raise ValueError("上下文日志引用了无效或重复的消息下标")
                    referenced.add(message_index)
                    events.append(
                        SessionLogEvent.append(seq, message_index, raw_messages[message_index])
                    )
                else:
                    events.append(
                        SessionLogEvent.replace(
                            seq,
                            int(raw["start"]),
                            int(raw["end"]),
                            message_from_dict(dict(raw["message"])),
                        )
                    )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"上下文日志损坏：{context_path}:{expected_seq + 1}") from exc

        missing = [index for index in range(len(raw_messages)) if index not in referenced]
        if missing:
            # 完整消息总是先落盘；进程若在随后写 context.jsonl 前退出，可安全补齐尾部引用。
            expected_missing = list(range(min(missing), len(raw_messages)))
            if missing != expected_missing:
                raise ValueError("上下文日志缺少非尾部消息引用，无法安全修复")
            for message_index in missing:
                event = SessionLogEvent.append(
                    len(events), message_index, raw_messages[message_index]
                )
                self._append_context_row_sync(
                    context_path,
                    {
                        "seq": event.seq,
                        "op": event.operation.value,
                        "messageIndex": message_index,
                    },
                )
                events.append(event)
        return tuple(events)

    def _append_context_event_sync(
        self,
        message_path: Path,
        context_path: Path,
        event: SessionLogEvent,
    ) -> None:
        events = self._context_events_sync(message_path, context_path)
        if event.operation is not SurfaceOperation.REPLACE:
            raise ValueError("SessionStorage 只接受由 SessionLog 创建的 Replace 事件")
        if event.seq != len(events):
            raise ValueError("SessionLog 已被并发修改，请重新加载后再压缩")
        assert event.replace_start is not None
        assert event.replace_end is not None
        self._append_context_row_sync(
            context_path,
            {
                "seq": event.seq,
                "op": event.operation.value,
                "start": event.replace_start,
                "end": event.replace_end,
                "message": message_to_dict(event.message),
            },
        )

    @staticmethod
    def _read_message_rows_sync(path: Path) -> list[Message]:
        if not path.exists():
            return []
        messages: list[Message] = []
        for line_number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                messages.append(message_from_dict(json.loads(line)))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"会话消息文件损坏：{path}:{line_number}") from exc
        return messages

    @staticmethod
    def _read_context_rows_sync(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError
                rows.append(value)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"上下文日志损坏：{path}:{line_number}") from exc
        return rows

    @staticmethod
    def _append_context_row_sync(path: Path, row: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _create_metadata_sync(
        metadata_path: Path,
        message_path: Path,
        context_path: Path,
        metadata: SessionMetadata,
    ) -> None:
        if metadata_path.exists() or message_path.exists() or context_path.exists():
            raise ValueError(f"会话已存在：{metadata.session_id}")
        JsonlSessionStorage._write_metadata_sync(metadata_path, metadata)

    @staticmethod
    def _write_metadata_sync(path: Path, metadata: SessionMetadata) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        payload = {
            "id": metadata.session_id,
            "name": metadata.name,
            "cwd": metadata.workspace,
            "startTime": metadata.created_at,
            "updatedTime": metadata.updated_at,
            "messageCount": metadata.message_count,
            "lastMessageType": metadata.last_message_type,
            "archived": metadata.archived,
            "version": metadata.schema_version,
        }
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    @staticmethod
    def _read_metadata_sync(path: Path) -> SessionMetadata:
        try:
            raw: dict[str, Any] = json.loads(path.read_text("utf-8"))
            last_message_type = raw.get("lastMessageType")
            return SessionMetadata(
                session_id=str(raw["id"]),
                name=str(raw["name"]),
                workspace=str(raw["cwd"]),
                created_at=str(raw["startTime"]),
                updated_at=str(raw["updatedTime"]),
                message_count=int(raw["messageCount"]),
                last_message_type=(
                    str(last_message_type) if last_message_type is not None else None
                ),
                archived=bool(raw["archived"]),
                schema_version=int(raw["version"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"会话元数据文件损坏：{path}") from exc

    def _list_metadata_sync(self) -> tuple[SessionMetadata, ...]:
        if not self._root.exists():
            return ()
        metadata: list[SessionMetadata] = []
        for path in self._root.iterdir():
            if not path.is_dir() or path.is_symlink() or not _SAFE_ID.fullmatch(path.name):
                continue
            metadata_path = path / "metadata.json"
            if metadata_path.exists():
                metadata.append(self._read_metadata_sync(metadata_path))
        return tuple(sorted(metadata, key=lambda item: item.updated_at, reverse=True))

    @staticmethod
    def _delete_sync(message_path: Path, context_path: Path, metadata_path: Path) -> bool:
        existed = message_path.exists() or context_path.exists() or metadata_path.exists()
        if message_path.exists():
            message_path.unlink()
        if context_path.exists():
            context_path.unlink()
        if metadata_path.exists():
            metadata_path.unlink()
        if existed:
            try:
                message_path.parent.rmdir()
            except OSError:
                # 未知扩展文件属于其他能力，删除核心文件时不级联清理。
                pass
        return existed


def _merge_adjacent_tool_messages(messages: list[Message]) -> tuple[Message, ...]:
    """合并连续工具结果，满足 Anthropic 对 tool_result 分组的要求。"""

    projected: list[Message] = []
    for message in messages:
        previous_is_tool = bool(projected and projected[-1].role is MessageRole.TOOL)
        if message.role is MessageRole.TOOL and previous_is_tool:
            previous = projected[-1]
            projected[-1] = Message(
                role=MessageRole.TOOL,
                content=previous.content + message.content,
            )
        else:
            projected.append(message)
    return tuple(projected)
