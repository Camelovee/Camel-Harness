from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
import stat
import tempfile
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

MemoryScope = Literal["project", "user"]
_SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
logger = logging.getLogger(__name__)


class MemoryConflictError(ValueError):
    """审批授权的版本已经变化，禁止静默覆盖或自动重试。"""


class MemoryStorageError(RuntimeError):
    """记忆提交故障；替换后确认失败可能已经产生持久副作用。"""


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """一条已确认记忆；归属来自存储根目录而非 JSON 正文。"""

    name: str
    description: str
    content: str
    scope: MemoryScope = "project"
    created_at: str = ""
    updated_at: str = ""
    revision: str = ""
    source: dict[str, str] = field(default_factory=dict)


def _tokens(text: str) -> set[str]:
    """中文双字片段用于召回，避免把整句中文误当作单个词。"""
    tokens = set(re.findall(r"[a-z0-9_-]+", text.lower()))
    for segment in re.findall(r"[\u3400-\u9fff]+", text):
        tokens.update(segment[index : index + 2] for index in range(len(segment) - 1))
        if len(segment) == 1:
            tokens.add(segment)
    return tokens


class MemoryStore:
    """项目与用户两层 JSON 记忆；写入使用文件锁和版本条件提交。"""

    def __init__(self, workspace: Path, user_root: Path | None = None) -> None:
        self._roots: dict[MemoryScope, Path] = {
            "project": workspace.resolve() / ".camel" / "memory",
            "user": user_root or Path.home().resolve() / ".camel" / "memory",
        }
        self._roots = {scope: root.expanduser().absolute() for scope, root in self._roots.items()}
        if self._roots["project"] == self._roots["user"]:
            raise ValueError("项目和用户记忆目录不能相同，请选择独立项目工作区或关闭记忆")

    async def list(
        self, scope: MemoryScope | None = None, *, tolerant: bool = False
    ) -> tuple[MemoryEntry, ...]:
        """默认投影有效集合；只在上下文读取时降级整个不可读的根目录。"""
        return await asyncio.to_thread(self._list_sync, scope, tolerant)

    async def search(
        self,
        query: str,
        *,
        scope: MemoryScope | None = None,
        limit: int = 5,
        tolerant: bool = False,
    ) -> tuple[MemoryEntry, ...]:
        if limit <= 0:
            raise ValueError("记忆检索条数必须大于 0")
        query_tokens = _tokens(query)
        entries = await self.list(scope, tolerant=tolerant)

        def score(entry: MemoryEntry) -> int:
            return 2 * len(query_tokens & _tokens(f"{entry.name} {entry.description}")) + len(
                query_tokens & _tokens(entry.content)
            )

        ranked = sorted(
            ((score(entry), entry) for entry in entries),
            key=lambda item: (-item[0], item[1].scope != "project", item[1].name),
        )
        return tuple(entry for matched, entry in ranked[:limit] if matched > 0)

    async def get(self, name: str, scope: MemoryScope = "project") -> MemoryEntry | None:
        return await asyncio.to_thread(self._get_sync, name, scope)

    async def save(
        self,
        entry: MemoryEntry,
        *,
        expected_revision: str,
        session_id: str,
        tool_call_id: str,
    ) -> MemoryEntry:
        """时间、版本和来源由可信执行上下文生成，不接受条目伪造。"""
        return await self._commit(
            entry.name, entry.scope, expected_revision, entry, session_id, tool_call_id
        )

    async def delete(self, name: str, scope: MemoryScope, *, expected_revision: str) -> bool:
        await self._commit(name, scope, expected_revision, None, "", "")
        return True

    def _root(self, scope: MemoryScope, *, create: bool = False) -> Path:
        if scope not in self._roots:
            raise ValueError("记忆层级必须是 project 或 user")
        root = self._roots[scope]
        # 固定根目录的每一层均不得是符号链接；避免 .camel 本身绕过边界。
        if any(path.is_symlink() for path in (root, *root.parents)):
            raise ValueError("记忆目录不得包含符号链接")
        if create:
            # parents=True 不将 mode 应用于中间目录，逐层创建私有目录。
            for path in reversed((root, *root.parents)):
                path.mkdir(exist_ok=True, mode=0o700)
        if root.exists() and not root.is_dir():
            raise ValueError("记忆根路径不是目录")
        return root

    def _path(self, name: str, scope: MemoryScope) -> Path:
        if not _SAFE_NAME.fullmatch(name):
            raise ValueError("Memory 名称包含不安全字符")
        path = self._root(scope) / f"{name}.json"
        if path.is_symlink():
            raise ValueError("记忆条目不得是符号链接")
        return path

    def _get_sync(self, name: str, scope: MemoryScope) -> MemoryEntry | None:
        path = self._path(name, scope)
        try:
            # O_NOFOLLOW 同时防止校验后的条目被替换成符号链接。
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError("记忆条目必须是普通文件")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            raw = json.load(stream)
        fields = {
            "name",
            "description",
            "content",
            "created_at",
            "updated_at",
            "revision",
            "source",
        }
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError("记忆格式无效，不兼容旧条目")
        if any(not isinstance(raw[key], str) for key in fields - {"source"}):
            raise ValueError("记忆文字字段无效")
        if raw["name"] != name or not re.fullmatch(r"[a-f0-9]{32}", raw["revision"]):
            raise ValueError("记忆名称或版本无效")
        for key in ("created_at", "updated_at"):
            if datetime.fromisoformat(raw[key]).utcoffset() != UTC.utcoffset(None):
                raise ValueError("记忆时间必须使用 UTC")
        source = raw["source"]
        if not isinstance(source, dict) or set(source) != {"session_id", "tool_call_id"}:
            raise ValueError("记忆来源无效")
        if any(not isinstance(value, str) or not value for value in source.values()):
            raise ValueError("记忆来源无效")
        return MemoryEntry(scope=scope, **raw)

    def _list_sync(self, scope: MemoryScope | None, tolerant: bool) -> tuple[MemoryEntry, ...]:
        if scope is not None and scope not in self._roots:
            raise ValueError("记忆层级必须是 project 或 user")
        entries: dict[str, MemoryEntry] = {}
        scopes: tuple[MemoryScope, ...] = (scope,) if scope is not None else ("user", "project")
        for selected in scopes:
            try:
                root = self._root(selected)
                paths = (
                    sorted(path for path in root.iterdir() if path.suffix == ".json")
                    if root.exists()
                    else []
                )
                for path in paths:
                    try:
                        entry = self._get_sync(path.stem, selected)
                        if entry is not None:
                            entries[entry.name] = entry
                    except (OSError, ValueError, UnicodeError):
                        logger.warning("跳过无效记忆条目：%s/%s", selected, path.stem)
            except (OSError, ValueError) as exc:
                if not tolerant:
                    raise
                logger.warning("记忆读取降级：%s（%s）", selected, type(exc).__name__)
        return tuple(entries[name] for name in sorted(entries))

    async def _commit(
        self,
        name: str,
        scope: MemoryScope,
        expected_revision: str,
        entry: MemoryEntry | None,
        session_id: str,
        tool_call_id: str,
    ) -> MemoryEntry:
        self._path(name, scope)
        if not re.fullmatch(r"0|[a-f0-9]{32}", expected_revision):
            raise ValueError("必须提供有效的期望记忆版本")
        if entry is not None and (not session_id or not tool_call_id):
            raise ValueError("保存记忆必须提供可信来源")
        if entry is not None and any(
            not isinstance(value, str) or not value
            for value in (entry.description, entry.content, session_id, tool_call_id)
        ):
            raise ValueError("记忆描述、正文和来源必须是非空文字")
        stop = threading.Event()
        task = asyncio.create_task(
            asyncio.to_thread(
                self._commit_sync,
                name,
                scope,
                expected_revision,
                entry,
                session_id,
                tool_call_id,
                stop,
            )
        )
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
                stop.set()
            except Exception:
                break
        if cancelled:
            if not task.cancelled() and task.exception() is not None:
                logger.warning("取消期间记忆提交失败：%s", type(task.exception()).__name__)
            raise asyncio.CancelledError
        return task.result()

    def _commit_sync(
        self,
        name: str,
        scope: MemoryScope,
        expected_revision: str,
        entry: MemoryEntry | None,
        session_id: str,
        tool_call_id: str,
        stop: threading.Event,
    ) -> MemoryEntry:
        published = False
        temporary: Path | None = None
        try:
            if stop.is_set():
                raise asyncio.CancelledError
            root = self._root(scope, create=True)
            descriptor = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "a+") as lock:
                if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                    raise ValueError("记忆锁必须是普通文件")
                while True:
                    if stop.is_set():
                        raise asyncio.CancelledError
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        stop.wait(0.01)
                if stop.is_set():
                    raise asyncio.CancelledError
                # 获得锁后再读版本，审批期间或其他进程的修改不能被覆盖。
                path = self._path(name, scope)
                previous = self._get_sync(name, scope)
                revision = previous.revision if previous else "0"
                if revision != expected_revision:
                    raise MemoryConflictError("记忆版本已变化，请重新读取并审批，不得自动重试")
                if entry is None:
                    if previous is None:
                        raise KeyError("记忆不存在")
                    path.unlink()
                    saved = previous
                else:
                    now = datetime.now(UTC).isoformat()
                    saved = replace(
                        entry,
                        created_at=previous.created_at if previous else now,
                        updated_at=now,
                        revision=uuid4().hex,
                        source={"session_id": session_id, "tool_call_id": tool_call_id},
                    )
                    payload = asdict(saved)
                    del payload["scope"]
                    with tempfile.NamedTemporaryFile(
                        mode="w", encoding="utf-8", dir=root, prefix=".memory-", delete=False
                    ) as stream:
                        temporary = Path(stream.name)
                        json.dump(payload, stream, ensure_ascii=False, indent=2)
                        stream.write("\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.replace(path)
                published = True
                directory_fd = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                return saved
        except OSError as exc:
            message = (
                "记忆提交后确认失败，结果不确定，请先读取确认，不得盲目重试"
                if published
                else "记忆存储失败，未提交修改"
            )
            raise MemoryStorageError(message) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logger.warning("记忆临时文件清理失败，需要检查存储目录")


def memory_scope(value: str) -> MemoryScope:
    """在工具边界将层级转换为受限类型，不允许任意目录。"""
    if value not in ("project", "user"):
        raise ValueError("记忆层级必须是 project 或 user")
    return cast(MemoryScope, value)
