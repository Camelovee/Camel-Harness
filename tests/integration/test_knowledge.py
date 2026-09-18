from camel_harness.agent.resources import MemoryEntry, MemoryStore, Skills, SkillSource


async def test_skill_evolution_creates_snapshot_and_can_rollback(tmp_path) -> None:
    skills = Skills(tmp_path, tmp_path / "user-skills")
    await skills.save("review", "代码审查", "先运行测试。", SkillSource.WORKSPACE)
    evolved = await skills.evolve("review", "检查依赖方向", "避免架构退化")
    assert evolved.version == "1.0.1"
    assert "检查依赖方向" in evolved.instructions
    restored = await skills.rollback("review")
    assert restored.version == "1.0.0"
    assert (tmp_path / ".camel/skill-evolution/audit.jsonl").is_file()


async def test_memory_is_isolated_under_workspace(tmp_path) -> None:
    memories = MemoryStore(tmp_path)
    await memories.save(MemoryEntry("decision", "架构决策", "使用 JSONL"))
    assert (await memories.get("decision")) == MemoryEntry("decision", "架构决策", "使用 JSONL")
    assert [item.name for item in await memories.list()] == ["decision"]
