"""隔离的记忆开关对照，只验证上下文协议，不衡量真实模型任务效果。"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from camel_harness.agent import Agent, AgentOptions
from camel_harness.agent.config import MemorySettings
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.resources import MemoryEntry, MemoryStore
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ToolExecutor, ToolRegistry
from camel_harness.agent.tools.memory_tools import MemoryToolProvider
from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    MessageCompleted,
    TextBlock,
    TextDelta,
)

_MODEL = LlmModel("fake", "fake-model")


@dataclass(frozen=True)
class Case:
    """期望正文是完整片段，检查条件是否被遗漏而不是精确自然语言回答。"""

    name: str
    query: str
    entries: tuple[MemoryEntry, ...]
    expected: tuple[str, ...]
    other_workspace: bool = False


CASES = (
    Case(
        "project-shadowing",
        "validation",
        (
            MemoryEntry("validation", "validation", "USER_VALIDATION", "user"),
            MemoryEntry("validation", "validation", "PROJECT_VALIDATION"),
        ),
        ("PROJECT_VALIDATION",),
    ),
    Case(
        "user-cross-project",
        "preference",
        (
            MemoryEntry("preference", "preference", "USER_PREFERENCE", "user"),
            MemoryEntry("preference", "preference", "PRIVATE_PROJECT_PREFERENCE"),
        ),
        ("USER_PREFERENCE",),
        other_workspace=True,
    ),
    Case(
        "chinese-recall",
        "修复数据库连接",
        (MemoryEntry("database", "数据库连接", "仅网络断开时重新连接，不重试写入。"),),
        ("仅网络断开时重新连接，不重试写入。",),
    ),
    Case(
        "english-body-recall",
        "DATABASE TIMEOUT",
        (MemoryEntry("network", "网络", "database timeout requires explicit cancellation"),),
        ("database timeout requires explicit cancellation",),
    ),
    Case(
        "condition-preservation",
        "审批策略",
        (MemoryEntry("approval", "审批策略", "即使 YOLO 模式也需审批；PLAN 模式禁止写入。"),),
        ("即使 YOLO 模式也需审批；PLAN 模式禁止写入。",),
    ),
    Case(
        "irrelevant-query",
        "修复前端布局",
        (MemoryEntry("network", "database timeout", "DATABASE_ONLY"),),
        (),
    ),
)


class ProbeAdapter(LlmAdapter):
    """确定性读取已传给模型的正文，不能用结果推断模型会正确利用记忆。"""

    def __init__(self, markers: tuple[str, ...]) -> None:
        self.markers = markers
        self.context_tokens = 0
        self.calls = 0

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.calls += 1
        self.context_tokens = (len(request.system_prompt.encode("utf-8")) + 2) // 3
        observed = [marker for marker in self.markers if marker in request.system_prompt]
        yield TextDelta(json.dumps(observed, ensure_ascii=False))
        yield MessageCompleted(LlmStopReason.STOP)


async def run_suite() -> dict[str, Any]:
    """同一假模型、查询与种子数据，分别开启和关闭记忆，无全局配置读写。"""
    results = []
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix="camel-memory-eval-") as temporary:
            root = Path(temporary).resolve()
            workspace = root / "project"
            workspace.mkdir()
            user_root = root / "user-memory"
            seed = MemoryStore(workspace, user_root)
            for entry in case.entries:
                await seed.save(
                    entry, expected_revision="0", session_id="seed", tool_call_id="seed"
                )
            if case.other_workspace:
                workspace = root / "other-project"
                workspace.mkdir()
            memories = MemoryStore(workspace, user_root)
            markers = tuple(entry.content for entry in case.entries)
            modes = []
            for enabled in (False, True):
                adapter = ProbeAdapter(markers)
                llm = LlmClient(adapter)
                registry = ToolRegistry()
                if enabled:
                    await registry.register_provider(MemoryToolProvider(memories))
                sessions = SessionManager(JsonlSessionStorage(root / "sessions"))
                session_id = "enabled" if enabled else "disabled"
                await sessions.create(session_id, workspace, case.name)
                agent = Agent(
                    sessions.bind(session_id),
                    workspace,
                    AgentOptions(),
                    llm,
                    registry,
                    ToolExecutor(registry),
                    ContextManager(
                        CompactionEngine(llm),
                        memories=memories,
                        memory_settings=MemorySettings(enabled=enabled),
                    ),
                    system_prompt="测试提示",
                )
                endings = []
                agent.subscribe(
                    lambda event, sink=endings: (
                        sink.append(event) if event.type == "agent_end" else None
                    )
                )
                try:
                    await agent.run(case.query, model=_MODEL)
                    messages = await sessions.bind(session_id).messages()
                    answer = "".join(
                        block.text
                        for message in messages
                        if isinstance(message, AssistantMessage)
                        for block in message.content
                        if isinstance(block, TextBlock)
                    )
                    observed = json.loads(answer) if answer else []
                    expected = case.expected if enabled else ()
                    modes.append(
                        {
                            "enabled": enabled,
                            "relevant_hits": len(set(observed) & set(case.expected)),
                            "wrong_references": len(set(observed) - set(expected)),
                            "missing_complete_conditions": len(set(expected) - set(observed)),
                            "estimated_context_tokens": adapter.context_tokens,
                            "llm_calls": adapter.calls,
                            "passed": set(observed) == set(expected)
                            and adapter.calls == 1
                            and len(endings) == 1
                            and endings[0].status == "completed",
                        }
                    )
                finally:
                    await agent.close()
            results.append(
                {"id": case.name, "passed": all(mode["passed"] for mode in modes), "modes": modes}
            )
    return {
        "kind": "memory_context_protocol",
        "provider": "fake",
        "model": "memory-fixture",
        "date_utc": datetime.now(UTC).isoformat(),
        "passed": sum(result["passed"] for result in results),
        "total": len(results),
        "results": results,
    }


def main() -> None:
    report = asyncio.run(run_suite())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
