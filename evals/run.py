"""运行隔离的编码任务集；fake 模式只检验 Harness，不代表模型能力。"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from camel_harness.agent import Agent, AgentOptions
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import (
    ApprovalHandler,
    PermissionMode,
    PermissionRequest,
    PermissionService,
    ToolExecutor,
    ToolRegistry,
    validate_tool_arguments,
)
from camel_harness.agent.tools.builtin import BuiltinToolProvider
from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmStreamEventType,
    TextBlock,
    ToolResultMessage,
    create_anthropic_adapter,
    create_openai_adapter,
)

TASKS_PATH = Path(__file__).with_name("tasks.json")


class EvalApproval(ApprovalHandler):
    """只批准评测工作区编辑和固定的本地测试命令。"""

    async def approve(
        self,
        request: PermissionRequest,
        reason: str,
        on_pending: Any = None,
    ) -> bool:
        if request.tool_name in {"write_file", "edit_file"}:
            return True
        return request.tool_name == "run_shell" and request.arguments.get("command") == (
            "python -m pytest -q"
        )


class ScriptedAdapter(LlmAdapter):
    """逐轮返回确定性事件，用来覆盖 Agent 的工具和恢复协议。"""

    def __init__(self, task: dict[str, Any]) -> None:
        self.task = task
        self.calls = 0

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.calls += 1
        if self.task.get("mode") == "resume" and self.calls == 1:
            yield LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": "未完成"})
            raise RuntimeError("评测注入：模型流中断")
        if self.task["calls"] and self.calls == 1:
            for index, call in enumerate(self.task["calls"]):
                yield LlmStreamEvent(
                    LlmStreamEventType.TOOL_CALL_COMPLETED,
                    {"call_id": f"eval-{index}", **call},
                )
            yield LlmStreamEvent(
                LlmStreamEventType.MESSAGE_COMPLETED,
                {"stop_reason": LlmStopReason.TOOL_USE.value},
            )
            return
        yield LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": self.task["answer"]})
        yield LlmStreamEvent(
            LlmStreamEventType.MESSAGE_COMPLETED,
            {"stop_reason": LlmStopReason.STOP.value},
        )


def load_tasks() -> list[dict[str, Any]]:
    """读取版本化任务集，拒绝重复 ID。"""

    tasks: list[dict[str, Any]] = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    ids = [task["id"] for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("评测任务 ID 重复")
    return tasks


def _workspace_path(workspace: Path, value: str) -> Path:
    workspace = workspace.resolve()
    path = (workspace / value).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError("评测任务路径越界")
    return path


def _adapter(provider: str, task: dict[str, Any]) -> LlmAdapter:
    if provider == "fake":
        return ScriptedAdapter(task)
    settings = AgentSettings.load()
    key_name = "anthropic_api_key" if provider == "anthropic" else "openai_api_key"
    key = settings.anthropic_api_key if provider == "anthropic" else settings.openai_api_key
    if not key:
        raise ValueError(f"真实 Provider 评测需要在 ~/.camel/config.yaml 配置 {key_name}")
    if provider == "anthropic":
        return create_anthropic_adapter(api_key=key, base_url=settings.anthropic_base_url)
    return create_openai_adapter(api_key=key, base_url=settings.openai_base_url)


def _grade(
    task: dict[str, Any], workspace: Path, messages: tuple[Any, ...], resumed: bool
) -> list[str]:
    answers = [
        block.text
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, TextBlock)
    ]
    tool_results = [message for message in messages if isinstance(message, ToolResultMessage)]
    failures: list[str] = []
    for check in task["checks"]:
        kind = check["kind"]
        if kind == "answer_contains":
            passed = check["value"] in "\n".join(answers)
        elif kind == "file_equals":
            path = _workspace_path(workspace, check["path"])
            passed = path.is_file() and path.read_text(encoding="utf-8") == check["value"]
        elif kind == "file_absent":
            passed = not _workspace_path(workspace, check["path"]).exists()
        elif kind == "tool_success":
            passed = bool(tool_results) and all(not item.is_error for item in tool_results)
        elif kind == "tool_error":
            passed = any(item.is_error for item in tool_results)
        elif kind == "resumed":
            passed = resumed
        else:
            raise ValueError(f"未知评测规则：{kind}")
        if not passed:
            failures.append(kind)
    return failures


async def run_task(task: dict[str, Any], provider: str, model: str) -> dict[str, Any]:
    """每个任务使用独立临时工作区和会话，避免互相污染。"""

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="camel-eval-") as root:
        workspace = Path(root) / "workspace"
        workspace.mkdir()
        for name, content in task["files"].items():
            path = _workspace_path(workspace, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        llm = LlmClient(_adapter(provider, task), max_attempts=1)
        sessions = SessionManager(JsonlSessionStorage(Path(root) / "sessions"))
        registry = ToolRegistry()
        await registry.register_provider(BuiltinToolProvider())
        agent = Agent(
            task["id"],
            workspace,
            AgentOptions(model, max_turns=4, max_duration_seconds=120, thinking=False),
            llm,
            registry,
            ToolExecutor(registry, validate_tool_arguments, PermissionService(EvalApproval())),
            sessions,
            ContextManager(CompactionEngine(llm)),
        )
        endings: list[Any] = []
        agent.subscribe(lambda event: endings.append(event) if event.type == "agent_end" else None)
        resumed = False
        try:
            await agent.run(task["prompt"], permission_mode=PermissionMode.DEFAULT)
            if provider == "fake" and task.get("mode") == "resume":
                await agent.resume(permission_mode=PermissionMode.DEFAULT)
                resumed = True
            messages = await sessions.messages(task["id"])
            failures = _grade(task, workspace, messages, resumed)
            if not endings or endings[-1].status != "completed":
                failures.append("run_not_completed")
            return {
                "id": task["id"],
                "passed": not failures,
                "failures": failures,
                "run_statuses": [event.status for event in endings],
                "stop_reason": endings[-1].stop_reason if endings else None,
                "run_error": endings[-1].error if endings else None,
                "tool_results": [
                    {
                        "name": item.name,
                        "is_error": item.is_error,
                        "content_preview": item.content[:240],
                    }
                    for item in messages
                    if isinstance(item, ToolResultMessage)
                ],
                "final_answer": "".join(
                    block.text
                    for message in messages
                    if isinstance(message, AssistantMessage)
                    for block in message.content
                    if isinstance(block, TextBlock)
                )[-500:],
                "total_tokens": sum(event.total_tokens for event in endings),
                "latency_seconds": round(time.monotonic() - started, 3),
            }
        finally:
            await agent.close()


async def run_suite(provider: str, model: str, selected: set[str] | None = None) -> dict[str, Any]:
    """真实 Provider 与离线脚本严格分开统计。"""

    tasks = load_tasks()
    if selected is not None:
        unknown = selected - {task["id"] for task in tasks}
        if unknown:
            raise ValueError(f"未知任务：{', '.join(sorted(unknown))}")
        tasks = [task for task in tasks if task["id"] in selected]
    if provider != "fake":
        tasks = [task for task in tasks if task.get("mode") != "resume"]
    results = []
    for task in tasks:
        try:
            results.append(await run_task(task, provider, model))
        except Exception as exc:
            results.append({"id": task["id"], "passed": False, "failures": [str(exc)]})
    passed = sum(result["passed"] for result in results)
    return {
        "provider": provider,
        "model": model,
        "date_utc": datetime.now(UTC).isoformat(),
        "kind": "harness_protocol" if provider == "fake" else "model_task_eval",
        "passed": passed,
        "total": len(results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="运行固定编码任务集")
    parser.add_argument("--provider", choices=("fake", "anthropic", "openai"), default="fake")
    parser.add_argument("--model", default="fixture")
    parser.add_argument("--task", action="append", help="任务 ID，可重复指定")
    parser.add_argument("--output", type=Path, help="可选 JSON 报告路径")
    args = parser.parse_args()
    if args.provider != "fake" and args.model == "fixture":
        parser.error("真实 Provider 运行必须显式指定 --model")
    report = asyncio.run(
        run_suite(args.provider, args.model, set(args.task) if args.task else None)
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
