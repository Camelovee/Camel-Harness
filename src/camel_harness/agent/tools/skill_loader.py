from __future__ import annotations

from ..resources.skills import Skills
from .registry import ToolProvider
from .types import Tool, ToolCall, ToolContext, ToolResult


class LoadSkillTool(Tool):
    """按需读取 Skill 正文或其支持文件，不提供写入和执行能力。"""

    name = "load_skill"
    description = (
        "先用名称加载 Skill 的 SKILL.md 正文；仅当正文指向支持文件时，"
        "再用 path 参数读取该 Skill 目录内的相对路径文本文件。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "path": {"type": "string", "minLength": 1},
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"skill.read"})

    def __init__(self, skills: Skills) -> None:
        self._skills = skills

    async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
        # Skill 根目录在构造时绑定，与单次调用的工作区无关。
        del tool_context
        name = str(tool_call.arguments["name"])
        relative_path = tool_call.arguments.get("path")
        if relative_path is None:
            content = await self._skills.load(name)
            return ToolResult(tool_call.call_id, tool_call.name, f"# Skill: {name}\n\n{content}")
        content = await self._skills.read_resource(name, str(relative_path))
        return ToolResult(
            tool_call.call_id, tool_call.name, f"# {name}/{relative_path}\n\n{content}"
        )


class SkillToolProvider(ToolProvider):
    """为完整运行时提供唯一只读 Skill 加载工具。"""

    def __init__(self, skills: Skills) -> None:
        self._skills = skills

    async def discover(self) -> tuple[Tool, ...]:
        return (LoadSkillTool(self._skills),)
