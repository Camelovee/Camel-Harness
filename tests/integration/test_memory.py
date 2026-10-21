from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import threading
from dataclasses import replace
from pathlib import Path

import portalocker
import pytest

from camel_harness.agent.config import MemorySettings
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.resources import MemoryEntry, MemoryStore
from camel_harness.agent.resources.memory import MemoryConflictError, MemoryStorageError
from camel_harness.agent.tools import (
    ApprovalHandler,
    PermissionMode,
    PermissionService,
    ToolCall,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
)
from camel_harness.agent.tools.filesystem import EditFileTool, WriteFileTool
from camel_harness.agent.tools.memory_tools import MemoryToolProvider
from camel_harness.llm import LlmAdapter, LlmClient


class _NoopLlm(LlmAdapter):
    """不流式的占位适配器：这些用例不触发压缩。"""

    @property
    def provider_id(self):
        return "fake"

    async def stream(self, llm_request):
        if False:
            yield None


@pytest.fixture
def memories(tmp_path):
    return MemoryStore(tmp_path / "project", tmp_path / "user-memory")


async def test_shared_directory_falls_back_to_single_layer(tmp_path):
    """工作区即主目录时项目层级回落到用户层级：同一物理目录只存一份，两层都可读。"""

    memory_store = MemoryStore(tmp_path, tmp_path / ".camel" / "memory")
    saved = await save(memory_store, scope="project")
    assert saved.scope == "project"
    assert [entry.name for entry in await memory_store.list()] == ["style"]
    assert (await memory_store.get("style", "user")) is not None
    assert len(list((tmp_path / ".camel" / "memory").glob("*.json"))) == 1


async def save(store, name="style", scope="project", content="使用中文注释", revision="0"):
    return await store.save(
        MemoryEntry(name, "编码规则", content, scope),
        expected_revision=revision,
        session_id="session",
        tool_call_id="call",
    )


async def test_layers_shadowing_and_reappearance(memories, tmp_path):
    user = await save(memories, scope="user", content="用户规则")
    project = await save(memories, content="项目规则")
    memory_store = MemoryStore(tmp_path / "other-project", tmp_path / "user-memory")
    assert await memories.list() == (project,)
    assert await memories.get("style", "user") == user
    assert await memory_store.list() == (user,)
    assert await memory_store.get("style") is None
    await memories.delete("style", "project", expected_revision=project.revision)
    assert await memories.list() == (user,)


async def test_metadata_update_and_forged_source(memories):
    initial = await save(memories)
    updated = await memories.save(
        replace(initial, content="新规则", source={"session_id": "fake"}, created_at="fake"),
        expected_revision=initial.revision,
        session_id="real-session",
        tool_call_id="real-call",
    )
    assert updated.created_at == initial.created_at
    assert updated.updated_at >= initial.updated_at
    assert updated.revision != initial.revision
    assert updated.source == {"session_id": "real-session", "tool_call_id": "real-call"}
    assert await memories.get("style") == updated


async def test_compare_and_swap_concurrency_and_recreation(memories, tmp_path):
    initial = await save(memories)
    memory_store = MemoryStore(tmp_path / "project", tmp_path / "user-memory")
    results = await asyncio.gather(
        save(memories, content="第一份", revision=initial.revision),
        save(memory_store, content="第二份", revision=initial.revision),
        return_exceptions=True,
    )
    assert sum(isinstance(result, MemoryEntry) for result in results) == 1
    assert sum(isinstance(result, MemoryConflictError) for result in results) == 1
    current = await memories.get("style")
    await memories.delete("style", "project", expected_revision=current.revision)
    recreated = await save(memories)
    assert recreated.revision != current.revision
    with pytest.raises(MemoryConflictError):
        await memories.delete("style", "project", expected_revision=current.revision)
    assert await memories.get("style") == recreated
    with pytest.raises(MemoryConflictError):
        await save(memories)
    with pytest.raises(KeyError, match="不存在"):
        await memories.delete("missing", "project", expected_revision="0")


def _process_save(workspace, user_root, revision, start, queue):
    """真实独立进程竞争同一个版本，不依赖进程内锁。"""
    start.wait(10)
    try:
        asyncio.run(save(MemoryStore(Path(workspace), Path(user_root)), revision=revision))
    except MemoryConflictError:
        queue.put("conflict")
    else:
        queue.put("saved")


async def test_cross_process_compare_and_swap(memories, tmp_path):
    initial = await save(memories)
    process_context = multiprocessing.get_context("spawn")
    start, queue = process_context.Event(), process_context.Queue()
    processes = [
        process_context.Process(
            target=_process_save,
            args=(
                str(tmp_path / "project"),
                str(tmp_path / "user-memory"),
                initial.revision,
                start,
                queue,
            ),
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        results = [await asyncio.to_thread(queue.get, True, 15) for _ in processes]
        assert sorted(results) == ["conflict", "saved"]
        for process in processes:
            await asyncio.to_thread(process.join, 10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(10)
        queue.close()


async def test_search_chinese_english_weighting_and_shadowing(memories):
    await save(memories, "shared", "user", "数据库连接")
    await save(memories, "shared", content="其他内容")
    chinese = await save(memories, "database", content="数据库连接需要超时")
    english = await save(memories, "timeout", content="database timeout")
    assert await memories.search("检查数据库连接") == (chinese,)
    assert await memories.search("DATABASE TIMEOUT", limit=1) == (english,)
    assert await memories.search("未曾出现的词语") == ()
    assert [entry.name for entry in await memories.search("编码规则")] == [
        "database",
        "shared",
        "timeout",
    ]
    assert (await memories.search("数据库连接", scope="user"))[0].name == "shared"


async def test_corruption_isolation_and_explicit_failure(memories, tmp_path, caplog):
    valid = await save(memories)
    root = tmp_path / "project" / ".camel" / "memory"
    (root / "broken.json").write_text('{"content":"secret-body"', encoding="utf-8")
    (root / "old.json").write_text('{"name":"old","kind":"project"}', encoding="utf-8")
    assert await memories.list() == (valid,)
    with pytest.raises(ValueError):
        await memories.get("old")
    with pytest.raises(ValueError):
        await memories.get("broken")
    assert "secret-body" not in caplog.text
    assert "broken" in caplog.text


@pytest.mark.parametrize("kind", ["directory", "fifo"])
async def test_special_files_do_not_block_context_loading(memories, tmp_path, kind):
    initial = await save(memories)
    path = tmp_path / "project" / ".camel" / "memory" / "invalid.json"
    if kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    with pytest.raises(ValueError, match="普通文件"):
        await asyncio.wait_for(memories.get("invalid"), 2)
    assert await asyncio.wait_for(memories.list(), 2) == (initial,)


async def test_unreadable_root_degrades_only_for_context(memories, monkeypatch, caplog):
    original = Path.iterdir

    def fail(path):
        if path.name == "memory":
            raise PermissionError("secret-error")
        return original(path)

    await save(memories)
    monkeypatch.setattr(Path, "iterdir", fail)
    assert await memories.search("规则", tolerant=True) == ()
    with pytest.raises(PermissionError):
        await memories.search("规则")
    assert "secret-error" not in caplog.text


@pytest.mark.parametrize("name", ["../escape", "/outside", ".hidden", "名字", "x" * 65])
async def test_names_cannot_escape(memories, name):
    with pytest.raises(ValueError, match="不安全"):
        await memories.get(name)
    with pytest.raises(ValueError, match="不安全"):
        await save(memories, name)


@pytest.mark.parametrize("target", ["entry", "root", "parent", "lock"])
async def test_symlink_boundaries(memories, tmp_path, target):
    initial = await save(memories)
    root = tmp_path / "project" / ".camel" / "memory"
    outside = tmp_path / "outside"
    if target in {"entry", "lock"}:
        outside.write_text("不能修改", encoding="utf-8")
        path = root / ("style.json" if target == "entry" else ".lock")
        path.unlink()
        path.symlink_to(outside)
    else:
        outside.mkdir()
        path = root if target == "root" else root.parent
        path.rename(path.with_name(path.name + "-backup"))
        path.symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, MemoryStorageError)):
        await save(memories, revision=initial.revision)
    if target != "lock":
        with pytest.raises(ValueError):
            await memories.get("style")
    if target in {"entry", "lock"}:
        assert outside.read_text("utf-8") == "不能修改"
    else:
        assert list(outside.iterdir()) == []


async def test_private_permissions_and_replace_failure(memories, tmp_path, monkeypatch):
    initial = await save(memories)
    root = tmp_path / "project" / ".camel" / "memory"
    assert root.stat().st_mode & 0o777 == 0o700
    assert root.parent.stat().st_mode & 0o777 == 0o700
    assert (root / "style.json").stat().st_mode & 0o777 == 0o600
    assert (root / ".lock").stat().st_mode & 0o777 == 0o600

    def fail(*args):
        raise OSError("敏感内部路径")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(MemoryStorageError, match="未提交"):
        await save(memories, revision=initial.revision)
    assert await memories.get("style") == initial
    assert not list(root.glob(".memory-*"))


async def test_post_commit_failure_reports_uncertain_result(memories, monkeypatch):
    original = os.fsync
    calls = 0

    def fail_directory(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("目录同步失败")
        return original(descriptor)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(MemoryStorageError, match="结果不确定"):
        await save(memories)
    assert (await memories.get("style")).content == "使用中文注释"


async def test_inflight_cancel_waits_for_commit_and_holds_lock(memories, monkeypatch):
    original = Path.replace
    entered, release = threading.Event(), threading.Event()

    def blocked(path, target):
        entered.set()
        assert release.wait(10)
        return original(path, target)

    monkeypatch.setattr(Path, "replace", blocked)
    task = asyncio.create_task(save(memories))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    contender = asyncio.create_task(save(memories, content="不应覆盖"))
    try:
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(MemoryConflictError):
            await contender
        assert (await memories.get("style")).content == "使用中文注释"
    finally:
        release.set()


async def test_queued_cancel_does_not_execute(memories, tmp_path):
    initial = await save(memories)
    lock_path = tmp_path / "project" / ".camel" / "memory" / ".lock"
    with lock_path.open("a+") as lock:
        portalocker.lock(lock, portalocker.LOCK_EX)
        task = asyncio.create_task(save(memories, content="不应写入", revision=initial.revision))
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    assert await memories.get("style") == initial


class Approval(ApprovalHandler):
    def __init__(self, accepted=True, before=None):
        self.accepted, self.before = accepted, before
        self.requests = []

    async def approve(self, permission_request, reason, on_pending=None):
        self.requests.append(permission_request)
        if self.before:
            await self.before()
        return self.accepted


async def executor(store, approval=None):
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(MemoryToolProvider(store))
    return ToolExecutor(tool_registry=tool_registry, permission_service=PermissionService(approval))


def save_call(**changes):
    arguments = {
        "scope": "project",
        "name": "style",
        "description": "编码规则",
        "content": "使用中文注释",
        "expected_revision": "0",
    }
    return ToolCall("trusted-call", "save_memory", arguments | changes)


@pytest.mark.parametrize("mode", list(PermissionMode))
async def test_approval_required_in_all_modes(memories, tmp_path, mode):
    approval = Approval()
    result = await (await executor(memories, approval)).execute(
        save_call(scope="user"), ToolContext("trusted-session", tmp_path, mode)
    )
    if mode is PermissionMode.PLAN:
        assert result.is_error and approval.requests == []
        assert await memories.get("style", "user") is None
    else:
        assert not result.is_error and len(approval.requests) == 1
        assert approval.requests[0].arguments == save_call(scope="user").arguments
        assert (await memories.get("style", "user")).source == {
            "session_id": "trusted-session",
            "tool_call_id": "trusted-call",
        }


@pytest.mark.parametrize("approval", [None, Approval(False)])
async def test_denied_or_missing_approval_never_writes(memories, tmp_path, approval):
    result = await (await executor(memories, approval)).execute(
        save_call(), ToolContext("s", tmp_path, PermissionMode.YOLO)
    )
    assert result.is_error
    assert await memories.list() == ()


async def test_approval_cancellation_and_conflict(memories, tmp_path):
    async def cancel():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await (await executor(memories, Approval(before=cancel))).execute(
            save_call(), ToolContext("s", tmp_path)
        )
    assert await memories.list() == ()

    async def concurrent_write():
        await save(memories, content="审批期间新增")

    result = await (await executor(memories, Approval(before=concurrent_write))).execute(
        save_call(), ToolContext("s", tmp_path)
    )
    assert result.is_error and "版本已变化" in result.content
    assert (await memories.get("style")).content == "审批期间新增"


async def test_tools_pagination_validation_and_delete(memories, tmp_path):
    saved = await save(memories, content="第一行\n第二行\n第三行")
    runner = await executor(memories, Approval())
    tool_context = ToolContext("s", tmp_path)
    searched = await runner.execute(ToolCall("c", "search_memory", {"query": "编码"}), tool_context)
    assert json.loads(searched.content)[0]["revision"] == saved.revision
    read = await runner.execute(
        ToolCall(
            "c",
            "read_memory",
            {
                "scope": "project",
                "name": "style",
                "offset": 1,
                "limit": 1,
            },
        ),
        tool_context,
    )
    header, text = read.content.split("\n", 1)
    assert json.loads(header)["next_offset"] == 2
    assert text == "2: 第二行"
    forged = await runner.execute(save_call(source={"session_id": "fake"}), tool_context)
    assert forged.is_error
    invalid = await runner.execute(
        ToolCall(
            "c",
            "search_memory",
            {
                "query": "编码",
                "limit": 21,
            },
        ),
        tool_context,
    )
    assert invalid.is_error
    deleted = await runner.execute(
        ToolCall(
            "c",
            "delete_memory",
            {
                "scope": "project",
                "name": "style",
                "expected_revision": saved.revision,
            },
        ),
        tool_context,
    )
    assert not deleted.is_error and await memories.get("style") is None


@pytest.mark.parametrize("tool", [WriteFileTool(), EditFileTool()])
@pytest.mark.parametrize("alias", [False, True])
async def test_file_tools_cannot_modify_managed_memory(tmp_path, monkeypatch, tool, alias):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".camel" / "memory"
    root.mkdir(parents=True)
    target = root / "style.json"
    target.write_text("old", encoding="utf-8")
    path = target
    if alias:
        path = tmp_path / "alias.json"
        path.symlink_to(target)
    with pytest.raises(ValueError, match="只能通过"):
        await tool.execute(
            ToolCall(
                "c",
                tool.name,
                {
                    "path": str(path),
                    "content": "new",
                    "old_text": "old",
                    "new_text": "new",
                },
            ),
            ToolContext("s", tmp_path),
        )
    assert target.read_text("utf-8") == "old"


async def test_context_budget_body_fallback_and_disabled(memories, tmp_path):
    await save(memories, "large", content="数据库" * 4000 + "不可省略的条件")
    await save(memories, "small", content="数据库需要超时")
    context_manager = ContextManager(
        system_prompt="测试提示",
        compaction_engine=CompactionEngine(llm_client=LlmClient(_NoopLlm())),
        memory_store=memories,
        memory_settings=MemorySettings(context_tokens=300),
    )
    result = await context_manager.build_system_context("数据库编码规则", tmp_path)
    assert "read_memory" in result and "不可省略的条件" not in result
    assert "数据库需要超时" in result
    assert (len(result.encode("utf-8")) + 2) // 3 <= 300
    assert "正文不是指令" in result
    assert await context_manager.build_system_context("无关信息", tmp_path) == ""
    disabled_context_manager = ContextManager(
        system_prompt="测试提示",
        compaction_engine=CompactionEngine(llm_client=LlmClient(_NoopLlm())),
        memory_store=memories,
        memory_settings=MemorySettings(enabled=False),
    )
    assert await disabled_context_manager.build_system_context("数据库", tmp_path) == ""
    minimal_context_manager = ContextManager(
        system_prompt="测试提示",
        compaction_engine=CompactionEngine(llm_client=LlmClient(_NoopLlm())),
        memory_store=memories,
        memory_settings=MemorySettings(context_tokens=1),
    )
    assert await minimal_context_manager.build_system_context("数据库", tmp_path) == ""


async def test_large_memory_read_uses_normal_result_archive(memories, tmp_path):
    from camel_harness.agent.session import SessionManager
    from camel_harness.agent.session.jsonl import JsonlSessionStorage
    from camel_harness.agent.tools.file_result_storage import FileToolResultStorage

    await save(memories, content="完整正文" * 20_000)
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("reader", tmp_path, "记忆读取")
    tool_result_storage = FileToolResultStorage(session_manager.directory)
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(MemoryToolProvider(memories))
    tool_executor = ToolExecutor(
        tool_registry=tool_registry, normalize_result=tool_result_storage.normalize
    )
    result = await tool_executor.execute(
        ToolCall("read", "read_memory", {"scope": "project", "name": "style"}),
        ToolContext("reader", tmp_path),
    )
    reference = result.metadata["content_ref"]
    assert not result.is_error
    path = Path(reference["path"])
    assert path.parent == session_manager.directory("reader") / "tool-results"
    assert "完整正文" * 20_000 in path.read_text("utf-8")


@pytest.mark.parametrize("mode", list(PermissionMode))
async def test_delete_requires_approval_even_in_yolo(memories, tmp_path, mode):
    entry = await save(memories)
    approval = Approval(False)
    runner = await executor(memories, approval)
    result = await runner.execute(
        ToolCall(
            "delete",
            "delete_memory",
            {
                "scope": "project",
                "name": "style",
                "expected_revision": entry.revision,
            },
        ),
        ToolContext("s", tmp_path, mode),
    )
    assert result.is_error
    assert len(approval.requests) == (0 if mode is PermissionMode.PLAN else 1)
    assert await memories.get("style") == entry
