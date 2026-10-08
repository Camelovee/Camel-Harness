"""显式运行真实 Provider 的文本、工具与取消烟测。"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from camel_harness.llm import LlmClient, LlmRequest, LlmStreamEventType, UserMessage

from .run import _adapter, load_tasks, run_suite


async def _cancel_stream(provider: str, model: str) -> bool:
    """取得首个文本增量后取消消费任务，确认取消会传入 SDK 流。"""

    adapter = _adapter(provider, {})
    client = LlmClient(adapter, max_attempts=1)
    first_delta = asyncio.Event()
    completed = False

    async def consume() -> None:
        nonlocal completed
        request = LlmRequest(
            model=model,
            messages=(UserMessage("请逐项列出从 1 到 100 的数字，每行一个数字。"),),
            max_output_tokens=1024,
        )
        async for event in client.stream(request):
            if event.type == LlmStreamEventType.TEXT_DELTA:
                first_delta.set()
            if event.type == LlmStreamEventType.MESSAGE_COMPLETED:
                completed = True

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(first_delta.wait(), timeout=60)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return not completed
        return False
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def smoke(provider: str, model: str) -> dict[str, object]:
    task_ids = {"text-only", "read-config"}
    available = {task["id"] for task in load_tasks()}
    if not task_ids <= available:
        raise ValueError("真实 Provider 烟测任务缺失")
    report = await run_suite(provider, model, task_ids)
    try:
        cancelled = await _cancel_stream(provider, model)
    except Exception as exc:
        cancelled = False
        cancellation_error = str(exc)
    else:
        cancellation_error = None
    return {
        "provider": provider,
        "model": model,
        "date_utc": datetime.now(UTC).isoformat(),
        "text_and_tool": report,
        "cancellation_passed": cancelled,
        "cancellation_error": cancellation_error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="真实 Provider 文本、工具和取消烟测")
    parser.add_argument("--provider", choices=("anthropic", "openai"), required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    report = asyncio.run(smoke(args.provider, args.model))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    tasks = report["text_and_tool"]
    assert isinstance(tasks, dict)
    if tasks["passed"] != tasks["total"] or not report["cancellation_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
