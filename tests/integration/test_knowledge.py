import pytest

from camel_harness.agent.resources import MemoryEntry, MemoryStore, Skills, SkillSource
from camel_harness.agent.tools import ToolCall, ToolContext, ToolExecutor, ToolRegistry
from camel_harness.agent.tools.executor import validate_tool_arguments
from camel_harness.agent.tools.skill_loader import SkillToolProvider


async def test_skill_discovery_and_three_stage_loading(tmp_path) -> None:
    workspace_skill = tmp_path / ".camel" / "skills" / "review"
    workspace_skill.mkdir(parents=True)
    (workspace_skill / "references").mkdir()
    (workspace_skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: >\n  审查代码结构\n  和测试覆盖\n---\n"
        "先检查测试。详细规则见 [检查表](references/checklist.md)。\n",
        encoding="utf-8",
    )
    (workspace_skill / "references" / "checklist.md").write_text("检查依赖方向", encoding="utf-8")
    skills = Skills(tmp_path, tmp_path / "user-skills")

    discovered = await skills.discover()
    review = next(item for item in discovered if item.name == "review")
    assert review.description == "审查代码结构 和测试覆盖"
    assert review.source is SkillSource.WORKSPACE
    assert not hasattr(review, "instructions")

    registry = ToolRegistry()
    await registry.register_provider(SkillToolProvider(skills))
    executor = ToolExecutor(registry, validate_tool_arguments)
    context = ToolContext("session-1", tmp_path)
    body = await executor.execute(ToolCall("call-1", "load_skill", {"name": "review"}), context)
    assert "先检查测试" in body.content
    assert "检查依赖方向" not in body.content

    reference = await executor.execute(
        ToolCall("call-2", "load_skill", {"name": "review", "path": "references/checklist.md"}),
        context,
    )
    assert "检查依赖方向" in reference.content


async def test_skill_workspace_precedence_and_resource_boundary(tmp_path) -> None:
    user_root = tmp_path / "user-skills"
    for root, text in (
        (user_root, "用户说明"),
        (tmp_path / ".camel" / "skills", "工作区说明"),
    ):
        directory = root / "review-change"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: review-change\ndescription: 代码审查\n---\n{text}\n", encoding="utf-8"
        )
    skills = Skills(tmp_path, user_root)

    review = next(item for item in await skills.discover() if item.name == "review-change")
    assert review.source is SkillSource.WORKSPACE
    assert await skills.load("review-change") == "工作区说明"
    with pytest.raises(ValueError, match="超出 Skill 目录"):
        await skills.read_resource("review-change", "../outside.md")
    with pytest.raises(ValueError, match="相对路径"):
        await skills.read_resource("review-change", "/etc/passwd")

    outside = tmp_path / "outside.md"
    outside.write_text("不应读取", encoding="utf-8")
    (tmp_path / ".camel" / "skills" / "review-change" / "escaped.md").symlink_to(outside)
    with pytest.raises(ValueError, match="超出 Skill 目录"):
        await skills.read_resource("review-change", "escaped.md")


async def test_builtin_skills_and_user_override(tmp_path) -> None:
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

    override = user_root / "review-change"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text(
        "---\nname: review-change\ndescription: 用户审查规则\n---\n用户规则\n", encoding="utf-8"
    )
    review = next(item for item in await skills.discover() if item.name == "review-change")
    assert review.source is SkillSource.USER
    assert await skills.load("review-change") == "用户规则"


async def test_skill_discovery_does_not_read_body(tmp_path) -> None:
    directory = tmp_path / ".camel" / "skills" / "lazy"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_bytes(b"---\nname: lazy\ndescription: lazy loading\n---\n\xff")
    skills = Skills(tmp_path, tmp_path / "user-skills")

    assert "lazy" in [item.name for item in await skills.discover()]
    with pytest.raises(UnicodeDecodeError):
        await skills.load("lazy")


async def test_memory_is_isolated_under_workspace(tmp_path) -> None:
    memories = MemoryStore(tmp_path)
    await memories.save(MemoryEntry("decision", "架构决策", "使用 JSONL"))
    assert (await memories.get("decision")) == MemoryEntry("decision", "架构决策", "使用 JSONL")
    assert [item.name for item in await memories.list()] == ["decision"]
