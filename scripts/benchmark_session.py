"""离线测量 JSONL 全量重放成本；只操作自动清理的临时会话。"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.codec import event_to_dict
from camel_harness.agent.session.events import SessionMessageEvent
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.llm import AssistantMessage, LlmStopReason, TextBlock, UserMessage


def seed(session_path: Path, count: int) -> None:
    """用正式编码器批量构造合法历史，避免将二次复杂度的建档耗时混入测量。"""
    timestamp = "2026-01-01T00:00:00+00:00"
    parent_entry_id = None
    with session_path.open("a", encoding="utf-8") as stream:
        for index in range(count):
            message = (
                UserMessage(f"任务 {index}：检查实现与测试")
                if index % 2 == 0
                else AssistantMessage((TextBlock(f"已完成检查 {index}"),), LlmStopReason.STOP)
            )
            entry = SessionMessageEvent(
                timestamp, message, id=f"entry-{index}", parent_id=parent_entry_id
            )
            stream.write(json.dumps(event_to_dict(entry), ensure_ascii=False) + "\n")
            parent_entry_id = entry.id


async def benchmark(sizes: list[int], repeats: int) -> dict[str, object]:
    benchmark_results = []
    for count in sizes:
        duration_samples: dict[str, list[float]] = {
            name: [] for name in ("append", "snapshot", "metadata", "list")
        }
        file_bytes = 0
        for _ in range(repeats):
            with tempfile.TemporaryDirectory(prefix="camel-session-benchmark-") as temporary:
                root = Path(temporary)
                session_manager = SessionManager(JsonlSessionStorage(root / "sessions"))
                await session_manager.create("benchmark", root, "性能测量")
                session_path = session_manager.directory("benchmark") / "session.jsonl"
                await asyncio.to_thread(seed, session_path, count)
                file_bytes = session_path.stat().st_size
                session = session_manager.bind("benchmark")
                started_at = perf_counter()
                await session.append_message(UserMessage("基准追加"))
                duration_samples["append"].append((perf_counter() - started_at) * 1000)
                started_at = perf_counter()
                snapshot = await session.context_snapshot()
                duration_samples["snapshot"].append((perf_counter() - started_at) * 1000)
                assert len(snapshot.messages) == count + 1
                started_at = perf_counter()
                metadata = await session_manager.get("benchmark")
                duration_samples["metadata"].append((perf_counter() - started_at) * 1000)
                assert metadata is not None and metadata.message_count == count + 1
                started_at = perf_counter()
                session_metadata = await session_manager.list()
                duration_samples["list"].append((perf_counter() - started_at) * 1000)
                assert len(session_metadata) == 1
        benchmark_results.append(
            {
                "initial_messages": count,
                "initial_bytes": file_bytes,
                "median_ms": {
                    name: round(statistics.median(values), 3)
                    for name, values in duration_samples.items()
                },
                "samples_ms": duration_samples,
            }
        )
    return {
        "date_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "repeats": repeats,
        "sessions_per_sample": 1,
        "note": "每次重复使用新临时日志；读取测量在追加一条消息后进行。无模型调用或耗时门槛。",
        "results": benchmark_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="测量临时 JSONL 会话的全量重放耗时")
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 10000])
    parser.add_argument("--repeats", type=int, default=3)
    arguments = parser.parse_args()
    if arguments.repeats < 1 or any(count < 1 for count in arguments.sizes):
        parser.error("消息数量与重复次数必须大于零")
    print(
        json.dumps(
            asyncio.run(benchmark(arguments.sizes, arguments.repeats)), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
