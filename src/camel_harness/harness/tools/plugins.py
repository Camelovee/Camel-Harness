from __future__ import annotations

from importlib.metadata import entry_points

from camel_harness.tools import ToolProvider
from camel_harness.types import Tool


class EntryPointToolProvider(ToolProvider):
    """通过 Python entry point 发现外部 Tool 或 ToolProvider。"""

    def __init__(self, group: str = "camel_harness.tools") -> None:
        self._group = group
        self.errors: list[str] = []

    async def discover(self) -> tuple[Tool, ...]:
        tools: list[Tool] = []
        for entry_point in entry_points(group=self._group):
            try:
                loaded = entry_point.load()
                candidate = loaded() if isinstance(loaded, type) else loaded
                if isinstance(candidate, Tool):
                    tools.append(candidate)
                    continue
                if isinstance(candidate, ToolProvider):
                    tools.extend(await candidate.discover())
                    continue
                raise TypeError(f"entry point {entry_point.name} 不是 Tool")
            except Exception as exc:
                self.errors.append(f"{entry_point.name}: {exc}")
        return tuple(tools)
