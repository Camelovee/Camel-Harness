from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ParamSpec, TextIO, TypeVar

import portalocker

from camel_harness.llm import Message

from ..platform_io import is_link
from .codec import event_from_dict, event_to_dict
from .events import (
    ContextSnapshot,
    SessionCompactionEvent,
    SessionEntry,
    SessionEvent,
    SessionHeaderEvent,
    SessionInfoEvent,
    SessionMessageEvent,
    build_branch,
    project_context,
    project_metadata,
)
from .metadata import SessionMetadata

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
logger = logging.getLogger(__name__)
_P = ParamSpec("_P")
_T = TypeVar("_T")


async def _complete_write(action: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """已开始的磁盘写入必须完成后才能释放会话锁和传播取消。"""
    return await _finish_commit(asyncio.to_thread(action, *args, **kwargs))


async def _finish_commit(operation: Awaitable[_T]) -> _T:
    """磁盘提交及关联游标必须一起完成，不能留下半更新的绑定会话。"""
    task = asyncio.ensure_future(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        try:
            task.result()
        except Exception as exc:
            logger.warning("取消期间会话写入失败：%s", type(exc).__name__)
        raise asyncio.CancelledError
    return task.result()


class JsonlSessionStorage:
    """每个会话目录一份追加日志；子会话位于父目录的 subagents 下。"""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._locks_by_session_id: dict[str, asyncio.Lock] = {}
        self._paths_by_session_id: dict[str, Path] = {}

    def directory(self, session_id: str) -> Path:
        """定位已存在会话，关联文件复用日志的真实目录。"""
        path = self._path(session_id)
        if not path.is_file():
            raise KeyError(f"会话不存在：{session_id}")
        return path.parent

    async def create(self, metadata: SessionMetadata) -> None:
        async with self._lock(metadata.session_id):
            path = self._new_path(metadata)
            await _complete_write(self._create_sync, path, metadata)
            self._paths_by_session_id[metadata.session_id] = path

    async def append_message(
        self,
        session_id: str,
        message: Message,
        *,
        leaf_id: str | None = None,
        use_latest: bool = True,
    ) -> str:
        async with self._lock(session_id):
            return await _complete_write(
                self._append_sync,
                self._path(session_id),
                message,
                leaf_id,
                use_latest,
            )

    async def compact(
        self,
        session_id: str,
        event: SessionCompactionEvent,
        expected: ContextSnapshot,
        *,
        leaf_id: str | None = None,
        use_latest: bool = True,
    ) -> ContextSnapshot:
        async with self._lock(session_id):
            return await _complete_write(
                self._append_compaction_sync,
                self._path(session_id),
                event,
                expected,
                leaf_id,
                use_latest,
            )

    async def messages(
        self, session_id: str, *, leaf_id: str | None = None, use_latest: bool = True
    ) -> tuple[Message, ...]:
        events = await self._events(session_id)
        return tuple(
            event.message
            for event in build_branch(events, leaf_id, use_latest=use_latest)
            if isinstance(event, SessionMessageEvent)
        )

    async def context_snapshot(
        self, session_id: str, *, leaf_id: str | None = None, use_latest: bool = True
    ) -> ContextSnapshot:
        return project_context(await self._events(session_id), leaf_id, use_latest=use_latest)

    async def entries(self, session_id: str) -> tuple[SessionEntry, ...]:
        return tuple(
            event
            for event in await self._events(session_id)
            if not isinstance(event, SessionHeaderEvent)
        )

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
            return await _complete_write(
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
            await _complete_write(self._delete_sync, path)
            self._paths_by_session_id.pop(session_id, None)
            return True

    async def _events(self, session_id: str) -> list[SessionEvent]:
        async with self._lock(session_id):
            return await asyncio.to_thread(self._read_events_sync, self._path(session_id))

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks_by_session_id.setdefault(session_id, asyncio.Lock())

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.fullmatch(session_id):
            raise ValueError("session_id 只能包含字母、数字、下划线和连字符")
        cached = self._paths_by_session_id.get(session_id)
        if cached is not None:
            self._assert_no_symlink(cached)
            if cached.exists():
                return cached
            self._paths_by_session_id.pop(session_id, None)
        direct = self._root / session_id / "session.jsonl"
        self._assert_no_symlink(direct)
        if direct.exists():
            self._paths_by_session_id[session_id] = direct
            return direct
        for path in self._session_paths():
            if path.parent.name == session_id:
                self._paths_by_session_id[session_id] = path
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
        if is_link(self._root):
            return True
        current = path
        while current != self._root:
            if is_link(current):
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
        event = SessionHeaderEvent(
            timestamp=metadata.created_at,
            session_id=metadata.session_id,
            workspace=metadata.workspace,
            name=metadata.name,
            model_id=metadata.model_id,
            archived=metadata.archived,
            parent_session_id=metadata.parent_session_id,
            parent_tool_call_id=metadata.parent_tool_call_id,
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _write_event(stream, event)

    @staticmethod
    def _delete_sync(path: Path) -> None:
        results = path.parent / "tool-results"
        if results.is_dir() and not is_link(results):
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
    def _append_sync(path: Path, message: Message, leaf_id: str | None, use_latest: bool) -> str:
        if not path.exists():
            raise KeyError(f"会话不存在：{path.parent.name}")
        with path.open("r+", encoding="utf-8") as stream:
            portalocker.lock(stream.fileno(), portalocker.LOCK_EX)
            try:
                snapshot = project_context(
                    JsonlSessionStorage._iter_events(stream, path), leaf_id, use_latest=use_latest
                )
                event = SessionMessageEvent(_now(), message, parent_id=snapshot.leaf_id)
                stream.seek(0, os.SEEK_END)
                _write_event(stream, event)
                return event.id
            finally:
                # Windows 字节区间锁从文件头释放；不依赖迭代中的 TextIO.tell()。
                stream.seek(0)
                portalocker.unlock(stream.fileno())

    @staticmethod
    def _append_compaction_sync(
        path: Path,
        event: SessionCompactionEvent,
        expected: ContextSnapshot,
        leaf_id: str | None,
        use_latest: bool,
    ) -> ContextSnapshot:
        if not path.exists():
            raise KeyError(f"会话不存在：{path.parent.name}")
        with path.open("r+", encoding="utf-8") as stream:
            portalocker.lock(stream.fileno(), portalocker.LOCK_EX)
            try:
                stream.seek(0)
                events = list(JsonlSessionStorage._iter_events(stream, path))
                snapshot = project_context(events, leaf_id, use_latest=use_latest)
                # 元数据不影响模型内容；分支变更或任意消息/压缩写入都会使旧计划失效。
                if expected.revision != snapshot.revision or expected.nodes != snapshot.nodes:
                    raise ValueError("会话上下文已被并发修改，请重新加载后再压缩")
                event = replace(event, parent_id=snapshot.leaf_id)
                updated = project_context([*events, event])
                stream.seek(0, os.SEEK_END)
                _write_event(stream, event)
                return updated
            finally:
                stream.seek(0)
                portalocker.unlock(stream.fileno())

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
            portalocker.lock(stream.fileno(), portalocker.LOCK_EX)
            try:
                events = list(JsonlSessionStorage._iter_events(stream, path))
                current = project_metadata(events)
                updated = current.update(
                    name=name, workspace=workspace, archived=archived, model_id=model_id
                )
                parent = events[-1]
                stream.seek(0, os.SEEK_END)
                _write_event(
                    stream,
                    SessionInfoEvent(
                        updated.updated_at,
                        updated.name,
                        updated.workspace,
                        updated.model_id,
                        updated.archived,
                        parent_id=parent.id if not isinstance(parent, SessionHeaderEvent) else None,
                    ),
                )
                return updated
            finally:
                stream.seek(0)
                portalocker.unlock(stream.fileno())

    @staticmethod
    def _read_events_sync(path: Path) -> list[SessionEvent]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as stream:
            portalocker.lock(stream.fileno(), portalocker.LOCK_SH)
            try:
                return list(JsonlSessionStorage._iter_events(stream, path))
            finally:
                stream.seek(0)
                portalocker.unlock(stream.fileno())

    @staticmethod
    def _read_metadata_sync(path: Path) -> SessionMetadata | None:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as stream:
            portalocker.lock(stream.fileno(), portalocker.LOCK_SH)
            try:
                return project_metadata(JsonlSessionStorage._iter_events(stream, path))
            finally:
                stream.seek(0)
                portalocker.unlock(stream.fileno())

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
    def _iter_events(stream: TextIO, path: Path) -> Iterator[SessionEvent]:
        """逐行解析并保存 ID 集合校验父引用，供上下文与元数据投影复用。"""

        found = False
        known_ids: set[str] = set()
        for number, line in enumerate(stream, start=1):
            try:
                if not line.endswith("\n"):
                    raise ValueError("会话日志尾部不完整")
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise TypeError("记录必须是 JSON 对象")
                event = event_from_dict(row)
                if number == 1:
                    if not isinstance(event, SessionHeaderEvent):
                        raise ValueError("会话头格式不受支持")
                    if event.session_id != path.parent.name:
                        raise ValueError("会话头 ID 与目录名不一致")
                    parent_id = (
                        path.parent.parent.parent.name
                        if path.parent.parent.name == "subagents"
                        else None
                    )
                    if event.parent_session_id != parent_id:
                        raise ValueError("会话头父 ID 与目录层级不一致")
                elif isinstance(event, SessionHeaderEvent):
                    raise ValueError("会话头只能出现一次")
                else:
                    if event.id in known_ids:
                        raise ValueError("会话条目 ID 重复")
                    if event.parent_id is not None and event.parent_id not in known_ids:
                        raise ValueError("会话父条目不存在或引用未来条目")
                    known_ids.add(event.id)
                found = True
                yield event
            except (TypeError, ValueError, KeyError, AttributeError) as exc:
                raise ValueError(f"会话日志损坏：{path}:{number}：{exc}") from exc
        if not found:
            raise ValueError(f"会话日志为空：{path}")


def _write_event(stream: TextIO, event: SessionEvent) -> None:
    stream.write(json.dumps(event_to_dict(event), ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def _now() -> str:
    return datetime.now(UTC).isoformat()
