from __future__ import annotations

from camel_harness.harness.resources import Skills, SkillSource
from camel_harness.types import Tool, ToolCall, ToolContext, ToolResult, ToolRisk


class _SkillTool(Tool):
    """为 Skill 工具保存共享资源访问对象。"""

    def __init__(self, skills: Skills) -> None:
        self.skills = skills


class SkillListTool(_SkillTool):
    """列出当前可用 Skill 的轻量摘要。"""

    name = "skill_list"
    description = "列出 builtin、用户级和工作区级 Skills。"
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        content = "\n".join(
            f"{item.name}\t{item.source.value}\t{item.description}"
            for item in await self.skills.discover()
        )
        return ToolResult(call.call_id, call.name, content or "暂无 Skill")


class SkillLoadTool(_SkillTool):
    """只在模型明确命中后加载 Skill 正文。"""

    name = "skill_load"
    description = "按名称加载一个 Skill 的完整指令。"
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 1}},
        "required": ["name"],
        "additionalProperties": False,
    }

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        skill = await self.skills.get(str(call.arguments["name"]))
        if skill is None:
            return ToolResult(call.call_id, call.name, "Skill 不存在", is_error=True)
        await self.skills.record_usage(skill.name, context.session_id)
        return ToolResult(call.call_id, call.name, skill.instructions)


class SkillCreateTool(_SkillTool):
    """创建可审计的工作区 Skill 文件。"""

    name = "skill_create"
    description = "在工作区 .camel/skills 中创建或更新一个 Skill。"
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "instructions": {"type": "string", "minLength": 1},
        },
        "required": ["name", "description", "instructions"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"skill.write", "filesystem.write"})
    risk = ToolRisk.WRITE

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        skill = await self.skills.save(
            str(call.arguments["name"]),
            str(call.arguments["description"]),
            str(call.arguments["instructions"]),
            SkillSource.WORKSPACE,
        )
        return ToolResult(call.call_id, call.name, f"已保存 Skill：{skill.name}")


class SkillEvolveTool(_SkillTool):
    """在版本快照和审计保护下演化现有 Skill。"""

    name = "skill_evolve"
    description = "向已有 Skill 合并一条可复用经验，并自动提升版本。"
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "lesson": {"type": "string", "minLength": 1},
            "rationale": {"type": "string", "default": ""},
        },
        "required": ["name", "lesson"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"skill.evolve", "filesystem.write"})
    risk = ToolRisk.WRITE

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        skill = await self.skills.evolve(
            str(call.arguments["name"]),
            str(call.arguments["lesson"]),
            str(call.arguments.get("rationale", "")),
        )
        return ToolResult(call.call_id, call.name, f"Skill 已演化至 {skill.version}")


class SkillRollbackTool(_SkillTool):
    """把工作区 Skill 回滚至最近快照。"""

    name = "skill_rollback"
    description = "将一个 Skill 回滚到最近的版本快照。"
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 1}},
        "required": ["name"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"skill.rollback", "filesystem.write"})
    risk = ToolRisk.WRITE

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        skill = await self.skills.rollback(str(call.arguments["name"]))
        return ToolResult(call.call_id, call.name, f"Skill 已回滚至 {skill.version}")
