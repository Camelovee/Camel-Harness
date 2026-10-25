"""两个固定记忆文件的底层存储：路径、锁、解析与原子提交。"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import threading
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

import portalocker

from ..platform_io import is_link, open_regular, sync_directory
from .types import MemoryStorageError, MemoryTarget, MemoryValidationError

_MEMORY_FILENAME = "MEMORY.md"
_USER_FILENAME = "USER.md"
ENTRY_SEPARATOR = "\n§\n"
# 外部文件读取的硬上限；与 MemorySettings 的业务字符上限是两回事。
_MAX_FILE_BYTES = 64 * 1024
logger = logging.getLogger(__name__)
_TransactionValue = TypeVar("_TransactionValue")


@dataclass(frozen=True, slots=True)
class MemoryWritePlan(Generic[_TransactionValue]):
    """锁内回调给出的提交计划；entries 为 None 表示不写文件。"""

    entries: tuple[str, ...] | None
    value: _TransactionValue


class MemoryStore:
    """按目标维护两个固定文件；不读取设置，也不决定内容策略。"""

    def __init__(self, workspace: Path, *, user_root: Path | None = None) -> None:
        self._roots_by_target: dict[MemoryTarget, Path] = {
            "memory": (workspace / ".camel" / "memory").expanduser().absolute(),
            "user": (user_root or Path.home() / ".camel" / "memory").expanduser().absolute(),
        }
        self._filenames_by_target: dict[MemoryTarget, str] = {
            "memory": _MEMORY_FILENAME,
            "user": _USER_FILENAME,
        }
        self._legacy_warned = False

    async def read_entries(self, target: MemoryTarget) -> tuple[str, ...]:
        """读取目标条目；文件缺失视为空，不创建目录。"""

        self._ensure_target(target)
        return await asyncio.to_thread(self._read_entries_sync, target)

    async def transact(
        self,
        target: MemoryTarget,
        operation: Callable[[tuple[str, ...]], MemoryWritePlan[_TransactionValue]],
        *,
        read_existing: bool = True,
    ) -> _TransactionValue:
        """在文件锁内读取现条目、执行回调并按计划原子提交。

        回调在工作线程中、持锁执行；它可以校验、匹配或计算上限，但不得递归
        调用本存储。回调抛出异常时不写文件并释放锁。取消只在取得锁之前生效；
        写入一旦开始，会等待提交完成后才传播取消。
        """

        self._ensure_target(target)
        return await self._run_in_thread(
            lambda stop: self._transact_sync(target, operation, read_existing, stop)
        )

    def _read_entries_sync(self, target: MemoryTarget) -> tuple[str, ...]:
        path = self._checked_path(target, create_root=False)
        if not path.exists():
            self._warn_legacy_entries(target)
            return ()
        return self._read_entries(path)

    def _transact_sync(
        self,
        target: MemoryTarget,
        operation: Callable[[tuple[str, ...]], MemoryWritePlan[_TransactionValue]],
        read_existing: bool,
        stop: threading.Event,
    ) -> _TransactionValue:
        with self._locked(target, stop) as path:
            try:
                entries = self._read_entries(path) if read_existing else ()
            except (OSError, ValueError) as exc:
                raise MemoryValidationError(f"现有记忆文件无法安全解析，未执行修改：{exc}") from exc
            plan = operation(entries)
            if plan.entries is not None:
                self._write_entries(path.parent, path, plan.entries)
            return plan.value

    @contextmanager
    def _locked(self, target: MemoryTarget, stop: threading.Event) -> Iterator[Path]:
        """锁内完成读取、匹配和提交；取消只阻止尚未开始的写入。"""

        root = self._root(target, create=True)
        lock_path = root / ".lock"
        if is_link(lock_path):
            raise MemoryValidationError("记忆锁文件不得是符号链接")
        descriptor = open_regular(lock_path, writable=True, create=True)
        with os.fdopen(descriptor, "a+") as lock:
            locked = False
            try:
                # 取消只在取锁前或刚取锁时生效；阻塞式锁无法中断，只能非阻塞轮询。
                while not locked:
                    if stop.is_set():
                        raise asyncio.CancelledError
                    try:
                        portalocker.lock(lock, portalocker.LOCK_EX | portalocker.LOCK_NB)
                        locked = True
                    except portalocker.exceptions.AlreadyLocked:
                        stop.wait(0.01)
                # 取锁后、写入前再检查一次：尚未开始的提交不执行。
                if stop.is_set():
                    raise asyncio.CancelledError
                yield self._checked_path(target, create_root=True)
            finally:
                if locked:
                    portalocker.unlock(lock)

    def _read_entries(self, path: Path) -> tuple[str, ...]:
        try:
            descriptor = open_regular(path)
        except FileNotFoundError:
            return ()
        with os.fdopen(descriptor, "rb") as stream:
            # 多读 1 字节用于判断是否超限，避免把超大文件整个读入内存。
            payload = stream.read(_MAX_FILE_BYTES + 1)
        if len(payload) > _MAX_FILE_BYTES:
            raise MemoryValidationError("记忆文件超过读取上限")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MemoryValidationError("记忆文件不是有效 UTF-8") from exc
        entries = parse_entries(text)
        if any(has_forbidden_characters(entry) for entry in entries):
            raise MemoryValidationError("记忆文件包含控制或不可见字符")
        return entries

    def _write_entries(self, root: Path, path: Path, entries: tuple[str, ...]) -> None:
        text = serialize_entries(entries)
        temporary: Path | None = None
        published = False
        try:
            # 临时文件必须与目标同目录，replace 才是原子发布。
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=root, prefix=".memory-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(text.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            temporary.replace(path)
            published = True
            # rename 后同步目录，确保目录项在崩溃后仍然可见。
            sync_directory(root)
        except OSError as exc:
            # 发布前失败表示未提交；发布后失败表示结果不确定，两者不能混为一谈。
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

    def _root(self, target: MemoryTarget, *, create: bool) -> Path:
        root = self._roots_by_target.get(target)
        if root is None:
            raise MemoryValidationError("记忆目标必须是 memory 或 user")
        if any(is_link(path) for path in (root, *root.parents)):
            # 任何祖先目录是链接都拒绝，避免读写被重定向到工作区之外。
            raise MemoryValidationError("记忆目录不得包含符号链接")
        if create:
            # 逐级创建并统一 0o700，避免中间目录沿用宽松的 umask。
            for path in reversed((root, *root.parents)):
                path.mkdir(exist_ok=True, mode=0o700)
        if root.exists() and not root.is_dir():
            raise MemoryValidationError("记忆根路径不是目录")
        return root

    def _checked_path(self, target: MemoryTarget, *, create_root: bool) -> Path:
        root = self._root(target, create=create_root)
        path = root / self._filenames_by_target[target]
        if is_link(path):
            raise MemoryValidationError("记忆文件不得是符号链接")
        return path

    def _warn_legacy_entries(self, target: MemoryTarget) -> None:
        if self._legacy_warned:
            return
        root = self._roots_by_target[target]
        try:
            has_legacy = root.is_dir() and any(path.suffix == ".json" for path in root.iterdir())
        except OSError:
            return
        if has_legacy:
            self._legacy_warned = True
            logger.warning("发现旧 JSON 记忆条目，运行时不再读取；请运行 scripts/migrate_memory.py")

    def _ensure_target(self, target: str) -> None:
        if target not in self._roots_by_target:
            raise MemoryValidationError("记忆目标必须是 memory 或 user")

    async def _run_in_thread(
        self, operation: Callable[[threading.Event], _TransactionValue]
    ) -> _TransactionValue:
        stop = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(operation, stop))
        cancelled = False
        # 与 _finish_commit 同构：取消只置 stop，已开始的提交仍要等线程结束后再传播。
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


def parse_entries(text: str) -> tuple[str, ...]:
    """按单独一行 § 拆分条目；统一换行并忽略空段。"""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return tuple(
        part.strip() for part in re.split(r"(?m)^[ \t]*§[ \t]*$", normalized) if part.strip()
    )


def serialize_entries(entries: Sequence[str]) -> str:
    """按记忆文件格式拼接条目；字符上限与落盘使用同一序列化。"""

    return ENTRY_SEPARATOR.join(entries)


def has_forbidden_characters(entry: str) -> bool:
    """拒绝控制、不可见和行分隔字符；换行与制表保留。"""

    for character in entry:
        if character in "\n\t":
            continue
        if unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            return True
    return False
