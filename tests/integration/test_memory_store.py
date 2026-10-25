from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import stat
import threading
from pathlib import Path

import portalocker
import pytest

from camel_harness.agent.memory import memory_store as store_module
from camel_harness.agent.memory.memory_store import (
    MemoryStore,
    MemoryWritePlan,
    parse_entries,
    serialize_entries,
)
from camel_harness.agent.memory.types import MemoryStorageError, MemoryValidationError


@pytest.fixture
def memory_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "project", user_root=tmp_path / "user-memory")


def _memory_path(tmp_path: Path) -> Path:
    return tmp_path / "project" / ".camel" / "memory" / "MEMORY.md"


async def _append(store: MemoryStore, target: str, content: str) -> None:
    def plan(entries: tuple[str, ...]) -> MemoryWritePlan[None]:
        return MemoryWritePlan((*entries, content), None)

    await store.transact(target, plan)


async def test_transact_reads_and_writes_under_lock(
    memory_store: MemoryStore, tmp_path: Path
) -> None:
    await _append(memory_store, "memory", "第一条")
    await _append(memory_store, "memory", "第二条")

    assert (await memory_store.read_entries("memory")) == ("第一条", "第二条")
    assert _memory_path(tmp_path).read_text(encoding="utf-8") == "第一条\n§\n第二条"
    assert serialize_entries(("第一条", "第二条")) == "第一条\n§\n第二条"


async def test_write_plan_without_entries_does_not_create_file(
    memory_store: MemoryStore, tmp_path: Path
) -> None:
    result = await memory_store.transact(
        "memory", lambda entries: MemoryWritePlan(None, len(entries))
    )

    assert result == 0
    assert not _memory_path(tmp_path).exists()


async def test_callback_failure_releases_lock_and_does_not_write(
    memory_store: MemoryStore, tmp_path: Path
) -> None:
    def fail(_entries: tuple[str, ...]) -> MemoryWritePlan[None]:
        raise RuntimeError("校验失败")

    with pytest.raises(RuntimeError, match="校验失败"):
        await memory_store.transact("memory", fail)
    assert not _memory_path(tmp_path).exists()

    await _append(memory_store, "memory", "后续写入")
    assert (await memory_store.read_entries("memory")) == ("后续写入",)


async def test_read_existing_false_overwrites_unparsable_file(
    memory_store: MemoryStore, tmp_path: Path
) -> None:
    path = _memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(MemoryValidationError):
        await _append(memory_store, "memory", "无法读取")

    def plan(_entries: tuple[str, ...]) -> MemoryWritePlan[None]:
        return MemoryWritePlan(("覆盖",), None)

    await memory_store.transact("memory", plan, read_existing=False)
    assert (await memory_store.read_entries("memory")) == ("覆盖",)


async def test_file_read_limit(memory_store: MemoryStore, tmp_path: Path) -> None:
    path = _memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"a" * 65536)
    assert len((await memory_store.read_entries("memory"))[0]) == 65536

    path.write_bytes(b"a" * 65537)
    with pytest.raises(MemoryValidationError, match="读取上限"):
        await memory_store.read_entries("memory")


async def test_parse_entries_normalizes_newlines() -> None:
    assert parse_entries("甲\r\n§\r\n乙\r§\n丙") == ("甲", "乙", "丙")


async def test_legacy_json_entries_are_ignored_with_warning(
    memory_store: MemoryStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "project" / ".camel" / "memory"
    root.mkdir(parents=True)
    (root / "style.json").write_text("{}", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        entries = await memory_store.read_entries("memory")

    assert entries == ()
    assert any("migrate_memory" in message for message in caplog.messages)


async def test_unreadable_target_raises(memory_store: MemoryStore, tmp_path: Path) -> None:
    path = _memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.mkdir()

    with pytest.raises(ValueError):
        await memory_store.read_entries("memory")


async def test_symlinked_memory_file_is_rejected(memory_store: MemoryStore, tmp_path: Path) -> None:
    root = tmp_path / "project" / ".camel" / "memory"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("外部内容", encoding="utf-8")
    (root / "MEMORY.md").symlink_to(outside)

    with pytest.raises(MemoryValidationError, match="符号链接"):
        await memory_store.read_entries("memory")
    with pytest.raises(MemoryValidationError, match="符号链接"):
        await _append(memory_store, "memory", "内容")


async def test_symlinked_lock_file_is_rejected(memory_store: MemoryStore, tmp_path: Path) -> None:
    root = tmp_path / "project" / ".camel" / "memory"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.lock"
    outside.write_text("", encoding="utf-8")
    (root / ".lock").symlink_to(outside)

    with pytest.raises(MemoryValidationError, match="符号链接"):
        await _append(memory_store, "memory", "内容")


@pytest.mark.skipif(os.name == "nt", reason="POSIX 权限位")
async def test_private_permissions_and_failed_replace(
    memory_store: MemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _append(memory_store, "memory", "旧内容")
    root = tmp_path / "project" / ".camel" / "memory"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "MEMORY.md").stat().st_mode) == 0o600

    def fail_replace(self: Path, target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(MemoryStorageError, match="未提交"):
        await _append(memory_store, "memory", "新内容")
    assert (root / "MEMORY.md").read_text(encoding="utf-8") == "旧内容"
    assert not list(root.glob(".memory-*"))


async def test_post_commit_failure_reports_uncertain_result(
    memory_store: MemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_sync(path: Path) -> None:
        raise OSError("sync failed")

    monkeypatch.setattr(store_module, "sync_directory", fail_sync)
    with pytest.raises(MemoryStorageError, match="结果不确定"):
        await _append(memory_store, "memory", "已写入")
    assert _memory_path(tmp_path).read_text(encoding="utf-8") == "已写入"


async def test_queued_cancel_does_not_execute(memory_store: MemoryStore, tmp_path: Path) -> None:
    root = tmp_path / "project" / ".camel" / "memory"
    root.mkdir(parents=True)
    descriptor = os.open(root / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a+") as lock:
        portalocker.lock(lock, portalocker.LOCK_EX)
        task = asyncio.create_task(_append(memory_store, "memory", "不应写入"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        portalocker.unlock(lock)
    assert not _memory_path(tmp_path).exists()


async def test_inflight_cancel_waits_for_commit(
    memory_store: MemoryStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = threading.Event(), threading.Event()
    original_write = memory_store._write_entries

    def blocking_write(root: Path, path: Path, entries: tuple[str, ...]) -> None:
        started.set()
        release.wait(5)
        original_write(root, path, entries)

    monkeypatch.setattr(memory_store, "_write_entries", blocking_write)
    task = asyncio.create_task(_append(memory_store, "memory", "提交中"))
    await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _memory_path(tmp_path).read_text(encoding="utf-8") == "提交中"


async def test_invalid_existing_file_blocks_transaction(
    memory_store: MemoryStore, tmp_path: Path
) -> None:
    path = _memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(MemoryValidationError, match="UTF-8"):
        await _append(memory_store, "memory", "内容")


async def test_external_edit_is_visible_on_next_read(
    memory_store: MemoryStore, tmp_path: Path
) -> None:
    path = _memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("外部条目", encoding="utf-8")
    assert (await memory_store.read_entries("memory")) == ("外部条目",)


def _process_add(workspace: str, user_root: str, content: str, start, queue) -> None:
    """真实独立进程同时追加，验证锁内读改写不丢更新。"""

    start.wait(10)
    try:

        async def run() -> None:
            memory_store = MemoryStore(Path(workspace), user_root=Path(user_root))
            await _append(memory_store, "memory", content)

        asyncio.run(run())
    except Exception as exc:
        queue.put(type(exc).__name__)
    else:
        queue.put("ok")


async def test_cross_process_adds_are_both_preserved(
    memory_store: MemoryStore, tmp_path: Path
) -> None:
    process_context = multiprocessing.get_context("spawn")
    start, queue = process_context.Event(), process_context.Queue()
    processes = [
        process_context.Process(
            target=_process_add,
            args=(
                str(tmp_path / "project"),
                str(tmp_path / "user-memory"),
                content,
                start,
                queue,
            ),
        )
        for content in ("甲", "乙")
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        results = [await asyncio.to_thread(queue.get, True, 15) for _ in processes]
        assert results == ["ok", "ok"]
        for process in processes:
            await asyncio.to_thread(process.join, 10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()

    entries = await memory_store.read_entries("memory")
    assert set(entries) == {"甲", "乙"}
