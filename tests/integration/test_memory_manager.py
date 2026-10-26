from __future__ import annotations

import json
from pathlib import Path

import pytest

from camel_harness.agent.config import MemorySettings
from camel_harness.agent.memory import (
    MemoryAmbiguousMatchError,
    MemoryLimitError,
    MemoryManager,
    MemoryNotFoundError,
    MemoryStorageError,
    MemoryTargetDisabledError,
    MemoryValidationError,
)
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


@pytest.fixture
def memory_manager(tmp_path: Path) -> MemoryManager:
    return MemoryManager(
        tmp_path / "project",
        MemorySettings(memory_char_limit=120, user_char_limit=80),
        user_root=tmp_path / "user-memory",
    )


def _memory_path(tmp_path: Path) -> Path:
    return tmp_path / "project" / ".camel" / "memory" / "MEMORY.md"


class Approval(ApprovalHandler):
    def __init__(self) -> None:
        self.requests = []

    async def approve(self, permission_request, reason, on_pending=None) -> bool:
        self.requests.append(permission_request)
        return True


async def _executor(
    memory_manager: MemoryManager, approval: ApprovalHandler | None = None
) -> ToolExecutor:
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(MemoryToolProvider(memory_manager))
    return ToolExecutor(tool_registry=tool_registry, permission_service=PermissionService(approval))


def _memory_call(action: str, target: str = "memory", **arguments: str) -> ToolCall:
    return ToolCall("trusted-call", "memory", {"action": action, "target": target, **arguments})


async def test_add_render_and_serialization(memory_manager: MemoryManager, tmp_path: Path) -> None:
    first = await memory_manager.add("memory", "第一条\n第二行")
    second = await memory_manager.add("memory", "第二条")

    assert first.changed and not first.duplicate
    assert second.document.entries == ("第一条\n第二行", "第二条")
    expected = "第一条\n第二行\n§\n第二条"
    assert second.document.text == expected
    assert second.document.used_chars == len(expected)
    assert _memory_path(tmp_path).read_text(encoding="utf-8") == expected

    prompt = await memory_manager.prompt_context()
    assert prompt.startswith("# 长期记忆")
    assert "MEMORY（Agent 笔记）" in prompt
    assert f"{len(expected)}/120 字符" in prompt
    assert expected in prompt


async def test_duplicate_add_and_repeat_replace_are_noops(
    memory_manager: MemoryManager,
) -> None:
    await memory_manager.add("memory", "同一条")

    duplicate = await memory_manager.add("memory", "同一条")
    assert duplicate.duplicate and not duplicate.changed

    unchanged = await memory_manager.replace("memory", "同一条", "同一条")
    assert not unchanged.changed and not unchanged.duplicate
    assert (await memory_manager.snapshot("memory")).entries == ("同一条",)


async def test_replace_and_remove_require_unique_match(
    memory_manager: MemoryManager, tmp_path: Path
) -> None:
    await memory_manager.add("memory", "偏好：中文注释")
    await memory_manager.add("memory", "偏好：简洁回答")

    with pytest.raises(MemoryAmbiguousMatchError) as ambiguous:
        await memory_manager.replace("memory", "偏好", "新内容")
    assert len(ambiguous.value.matches) == 2
    with pytest.raises(MemoryNotFoundError):
        await memory_manager.remove("memory", "不存在")

    replaced = await memory_manager.replace("memory", "中文注释", "代码注释使用中文")
    assert replaced.document.entries == ("代码注释使用中文", "偏好：简洁回答")
    removed = await memory_manager.remove("memory", "简洁回答")
    assert removed.document.entries == ("代码注释使用中文",)

    await memory_manager.remove("memory", "代码注释")
    assert (await memory_manager.snapshot("memory")).entries == ()
    assert _memory_path(tmp_path).read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    "content",
    ["", "   ", "第一行\n§\n第二行", "bad\x00char", "zero​width", "line sep"],
)
async def test_content_validation_rejects_unsafe_entries(
    memory_manager: MemoryManager, content: str
) -> None:
    with pytest.raises(MemoryValidationError):
        await memory_manager.add("memory", content)
    assert (await memory_manager.snapshot("memory")).entries == ()


async def test_content_validation_keeps_newline_and_tab(
    memory_manager: MemoryManager,
) -> None:
    mutation = await memory_manager.add("memory", "第一行\t缩进\n第二行")
    assert mutation.changed


async def test_limit_boundary_and_error_details(tmp_path: Path) -> None:
    memory_manager = MemoryManager(
        tmp_path / "project",
        MemorySettings(memory_char_limit=20),
        user_root=tmp_path / "user-memory",
    )
    assert (await memory_manager.add("memory", "a" * 20)).changed

    with pytest.raises(MemoryLimitError) as caught:
        await memory_manager.add("memory", "b")
    error = caught.value
    assert error.used_chars == 20
    assert error.candidate_chars == 24
    assert error.limit_chars == 20
    assert error.entries == ("a" * 20,)

    with pytest.raises(MemoryLimitError):
        await memory_manager.add("memory", "x" * 21)

    await memory_manager.remove("memory", "a" * 10)
    assert (await memory_manager.add("memory", "b")).changed


async def test_external_over_limit_file_can_only_shrink(tmp_path: Path) -> None:
    memory_manager = MemoryManager(
        tmp_path / "project",
        MemorySettings(memory_char_limit=20),
        user_root=tmp_path / "user-memory",
    )
    path = _memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("a" * 30, encoding="utf-8")

    with pytest.raises(MemoryLimitError):
        await memory_manager.add("memory", "b")
    prompt = await memory_manager.prompt_context()
    assert "超过字符上限" in prompt

    replaced = await memory_manager.replace("memory", "a" * 10, "短")
    assert replaced.changed
    assert (await memory_manager.snapshot("memory")).entries == ("短",)


async def test_target_flags_control_injection_and_writes(tmp_path: Path) -> None:
    memory_manager = MemoryManager(
        tmp_path / "project",
        MemorySettings(user_profile_enabled=False),
        user_root=tmp_path / "user-memory",
    )
    await memory_manager.add("memory", "工作区记忆")
    with pytest.raises(MemoryTargetDisabledError):
        await memory_manager.add("user", "用户偏好")
    prompt = await memory_manager.prompt_context()
    assert "工作区记忆" in prompt
    assert "USER PROFILE" not in prompt

    disabled_manager = MemoryManager(
        tmp_path / "other",
        MemorySettings(enabled=False),
        user_root=tmp_path / "other-user",
    )
    assert not (await disabled_manager.snapshot("memory")).enabled
    assert await disabled_manager.prompt_context() == ""
    with pytest.raises(MemoryTargetDisabledError):
        await disabled_manager.add("memory", "内容")


async def test_same_root_keeps_two_independent_files(tmp_path: Path) -> None:
    root = tmp_path / ".camel" / "memory"
    memory_manager = MemoryManager(tmp_path, MemorySettings(), user_root=root)
    await memory_manager.add("memory", "项目笔记")
    await memory_manager.add("user", "用户偏好")

    assert (root / "MEMORY.md").read_text(encoding="utf-8") == "项目笔记"
    assert (root / "USER.md").read_text(encoding="utf-8") == "用户偏好"
    prompt = await memory_manager.prompt_context()
    assert "项目笔记" in prompt and "用户偏好" in prompt


async def test_unreadable_target_degrades_only_for_prompt(
    memory_manager: MemoryManager, tmp_path: Path
) -> None:
    path = _memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.mkdir()

    with pytest.raises(ValueError):
        await memory_manager.snapshot("memory")
    assert await memory_manager.prompt_context() == ""


async def test_symlinked_memory_file_is_rejected_before_write(
    memory_manager: MemoryManager, tmp_path: Path
) -> None:
    root = tmp_path / "project" / ".camel" / "memory"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("外部内容", encoding="utf-8")
    (root / "MEMORY.md").symlink_to(outside)

    with pytest.raises(MemoryValidationError, match="符号链接"):
        await memory_manager.snapshot("memory")
    assert await memory_manager.prompt_context() == ""
    with pytest.raises(MemoryValidationError, match="符号链接"):
        await memory_manager.add("memory", "内容")


async def test_invalid_existing_file_blocks_mutation_but_not_prompt(
    memory_manager: MemoryManager, tmp_path: Path
) -> None:
    path = _memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(MemoryValidationError, match="UTF-8"):
        await memory_manager.add("memory", "内容")
    assert await memory_manager.prompt_context() == ""


async def test_external_edit_is_visible_on_next_read(
    memory_manager: MemoryManager, tmp_path: Path
) -> None:
    path = _memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("外部条目", encoding="utf-8")
    assert (await memory_manager.snapshot("memory")).entries == ("外部条目",)


async def test_memory_tool_updates_and_reports_live_state(
    memory_manager: MemoryManager, tmp_path: Path
) -> None:
    runner = await _executor(memory_manager)
    result = await runner.execute(
        _memory_call("add", content="偏好 A"), ToolContext("session", tmp_path)
    )

    payload = json.loads(result.content)
    assert not result.is_error
    assert payload["changed"] and not payload["duplicate"]
    assert payload["usage"] == "4/120"
    assert payload["memory"] == "偏好 A"


@pytest.mark.parametrize(
    "mode", [PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS, PermissionMode.YOLO]
)
async def test_memory_writes_do_not_require_approval(
    memory_manager: MemoryManager, tmp_path: Path, mode: PermissionMode
) -> None:
    approval = Approval()
    runner = await _executor(memory_manager, approval)
    result = await runner.execute(
        _memory_call("add", content="无需审批"), ToolContext("session", tmp_path, mode)
    )

    assert not result.is_error
    assert approval.requests == []
    assert (await memory_manager.snapshot("memory")).entries == ("无需审批",)


async def test_plan_mode_denies_memory_writes(
    memory_manager: MemoryManager, tmp_path: Path
) -> None:
    approval = Approval()
    runner = await _executor(memory_manager, approval)
    result = await runner.execute(
        _memory_call("add", content="计划模式"),
        ToolContext("session", tmp_path, PermissionMode.PLAN),
    )

    assert result.is_error
    assert "Plan Mode" in result.content
    assert approval.requests == []
    assert (await memory_manager.snapshot("memory")).entries == ()


async def test_memory_tool_marks_validation_failure_not_executed(
    memory_manager: MemoryManager, tmp_path: Path
) -> None:
    runner = await _executor(memory_manager)
    await memory_manager.add("memory", "甲：内容")
    await memory_manager.add("memory", "乙：内容")

    result = await runner.execute(
        _memory_call("remove", old_text="内容"), ToolContext("session", tmp_path)
    )

    assert result.is_error
    assert result.metadata["execution_status"] == "not_executed"
    assert (await memory_manager.snapshot("memory")).entries == ("甲：内容", "乙：内容")


async def test_memory_tool_marks_storage_failure_unknown(
    memory_manager: MemoryManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args: object) -> None:
        raise MemoryStorageError("记忆提交后确认失败，结果不确定")

    monkeypatch.setattr(memory_manager, "add", fail)
    runner = await _executor(memory_manager)
    result = await runner.execute(
        _memory_call("add", content="内容"), ToolContext("session", tmp_path)
    )

    assert result.is_error
    assert result.metadata["execution_status"] == "unknown"
    assert "当前记忆" in result.content


@pytest.mark.parametrize("tool", [WriteFileTool(), EditFileTool()])
@pytest.mark.parametrize("alias", [False, True])
async def test_file_tools_cannot_modify_managed_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool, alias: bool
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".camel" / "memory"
    root.mkdir(parents=True)
    target = root / "MEMORY.md"
    target.write_text("旧内容", encoding="utf-8")
    path = target
    if alias:
        path = tmp_path / "alias.md"
        path.symlink_to(target)
    arguments = (
        {"path": str(path), "content": "新内容"}
        if isinstance(tool, WriteFileTool)
        else {"path": str(path), "edits": [{"old_text": "旧内容", "new_text": "新内容"}]}
    )
    with pytest.raises(ValueError, match="只能通过"):
        await tool.execute(
            ToolCall("c", tool.name, arguments),
            ToolContext("s", tmp_path),
        )
    assert target.read_text("utf-8") == "旧内容"
