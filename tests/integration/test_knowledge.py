import pytest

from camel_harness.agent.config import MemorySettings
from camel_harness.agent.resources import MemoryStore, Skills, SkillSource
from camel_harness.agent.tools import ToolCall, ToolContext, ToolExecutor, ToolRegistry
from camel_harness.agent.tools.skill_loader import SkillToolProvider


async def test_skill_discovery_and_three_stage_loading(tmp_path) -> None:
    project_skill = tmp_path / ".camel" / "skills" / "review"
    project_skill.mkdir(parents=True)
    (project_skill / "references").mkdir()
    (project_skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: >\n  审查代码结构\n  和测试覆盖\n---\n"
        "先检查测试。详细规则见 [检查表](references/checklist.md)。\n",
        encoding="utf-8",
    )
    (project_skill / "references" / "checklist.md").write_text("检查依赖方向", encoding="utf-8")
    skills = Skills(tmp_path, tmp_path / "user-skills")

    discovered = await skills.discover()
    review = next(item for item in discovered if item.name == "review")
    assert review.description == "审查代码结构 和测试覆盖"
    assert review.source is SkillSource.PROJECT
    assert not hasattr(review, "instructions")

    tool_registry = ToolRegistry()
    await tool_registry.register_provider(SkillToolProvider(skills))
    tool_executor = ToolExecutor(tool_registry=tool_registry)
    tool_context = ToolContext("session-1", tmp_path)
    body = await tool_executor.execute(
        ToolCall("call-1", "load_skill", {"name": "review"}), tool_context
    )
    assert "先检查测试" in body.content
    assert "检查依赖方向" not in body.content

    reference = await tool_executor.execute(
        ToolCall("call-2", "load_skill", {"name": "review", "path": "references/checklist.md"}),
        tool_context,
    )
    assert "检查依赖方向" in reference.content


async def test_project_skill_precedence_over_user_and_resource_boundary(tmp_path) -> None:
    user_root = tmp_path / "user-skills"
    for root, text in (
        (user_root, "用户说明"),
        (tmp_path / ".camel" / "skills", "项目说明"),
    ):
        directory = root / "review"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: review\ndescription: 代码审查\n---\n{text}\n", encoding="utf-8"
        )
    skills = Skills(tmp_path, user_root)

    review = next(item for item in await skills.discover() if item.name == "review")
    assert review.source is SkillSource.PROJECT
    assert await skills.load("review") == "项目说明"
    user_only = user_root / "user-only"
    user_only.mkdir()
    (user_only / "SKILL.md").write_text(
        "---\nname: user-only\ndescription: 用户专属\n---\n用户内容\n", encoding="utf-8"
    )
    assert next(item for item in await skills.discover() if item.name == "user-only").source is (
        SkillSource.USER
    )
    with pytest.raises(ValueError, match="超出 Skill 目录"):
        await skills.read_resource("review", "../outside.md")
    with pytest.raises(ValueError, match="相对路径"):
        await skills.read_resource("review", "/etc/passwd")

    outside = tmp_path / "outside.md"
    outside.write_text("不应读取", encoding="utf-8")
    (tmp_path / ".camel" / "skills" / "review" / "escaped.md").symlink_to(outside)
    with pytest.raises(ValueError, match="超出 Skill 目录"):
        await skills.read_resource("review", "escaped.md")


async def test_builtin_skills_override_project_and_user(tmp_path) -> None:
    user_root = tmp_path / "user-skills"
    skills = Skills(tmp_path, user_root)
    discovered = {item.name: item for item in await skills.discover()}

    assert set(discovered) == {
        "explore-codebase",
        "debug-issue",
        "review-change",
        "verify-change",
    }
    assert all(item.source is SkillSource.BUILTIN for item in discovered.values())
    assert "审查变更" in await skills.load("review-change")
    assert "输入校验" not in await skills.load("review-change")
    assert "输入校验" in await skills.read_resource("review-change", "references/checklist.md")

    for root, description, body in (
        (user_root, "用户审查规则", "用户规则"),
        (tmp_path / ".camel" / "skills", "项目审查规则", "项目规则"),
    ):
        override = root / "review-change"
        override.mkdir(parents=True)
        (override / "SKILL.md").write_text(
            f"---\nname: review-change\ndescription: {description}\n---\n{body}\n",
            encoding="utf-8",
        )
    review = next(item for item in await skills.discover() if item.name == "review-change")
    assert review.source is SkillSource.BUILTIN
    assert review.description == discovered["review-change"].description
    assert "审查变更" in await skills.load("review-change")
    assert "输入校验" in await skills.read_resource("review-change", "references/checklist.md")


async def test_skill_discovery_does_not_read_body(tmp_path) -> None:
    directory = tmp_path / ".camel" / "skills" / "lazy"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_bytes(b"---\nname: lazy\ndescription: lazy loading\n---\n\xff")
    skills = Skills(tmp_path, tmp_path / "user-skills")

    assert "lazy" in [item.name for item in await skills.discover()]
    with pytest.raises(UnicodeDecodeError):
        await skills.load("lazy")


async def test_memory_is_isolated_under_workspace(tmp_path) -> None:
    memory_store = MemoryStore(tmp_path, MemorySettings(), user_root=tmp_path / "user-memory")
    mutation = await memory_store.add("memory", "使用 JSONL")
    assert mutation.changed
    document = await memory_store.snapshot("memory")
    assert document.entries == ("使用 JSONL",)
    assert (tmp_path / ".camel" / "memory" / "MEMORY.md").exists()

    other_store = MemoryStore(
        tmp_path / "other-workspace",
        MemorySettings(),
        user_root=tmp_path / "user-memory",
    )
    assert (await other_store.snapshot("memory")).entries == ()
