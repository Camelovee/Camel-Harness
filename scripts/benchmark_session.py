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
    parent = None
    with session_path.open("a", encoding="utf-8") as stream:
        for index in range(count):
            message = (
                UserMessage(f"任务 {index}：检查实现与测试")
                if index % 2 == 0
                else AssistantMessage((TextBlock(f"已完成检查 {index}"),), LlmStopReason.STOP)
            )
            entry = SessionMessageEvent(timestamp, message, id=f"entry-{index}", parent_id=parent)
            stream.write(json.dumps(event_to_dict(entry), ensure_ascii=False) + "\n")
            parent = entry.id


async def benchmark(sizes: list[int], repeats: int) -> dict[str, object]:
    results = []
    for count in sizes:
        samples: dict[str, list[float]] = {
            name: [] for name in ("append", "snapshot", "metadata", "list")
        }
        file_bytes = 0
        for _ in range(repeats):
            with tempfile.TemporaryDirectory(prefix="camel-session-benchmark-") as temporary:
                root = Path(temporary)
                sessions = SessionManager(JsonlSessionStorage(root / "sessions"))
                await sessions.create("benchmark", root, "性能测量")
                path = sessions.directory("benchmark") / "session.jsonl"
                await asyncio.to_thread(seed, path, count)
                file_bytes = path.stat().st_size
                session = sessions.bind("benchmark")
                start = perf_counter()
                await session.append_message(UserMessage("基准追加"))
                samples["append"].append((perf_counter() - start) * 1000)
                start = perf_counter()
                snapshot = await session.context_snapshot()
                samples["snapshot"].append((perf_counter() - start) * 1000)
                assert len(snapshot.messages) == count + 1
                start = perf_counter()
                metadata = await sessions.get("benchmark")
                samples["metadata"].append((perf_counter() - start) * 1000)
                assert metadata is not None and metadata.message_count == count + 1
                start = perf_counter()
                records = await sessions.list()
                samples["list"].append((perf_counter() - start) * 1000)
                assert len(records) == 1
        results.append(
            {
                "initial_messages": count,
                "initial_bytes": file_bytes,
                "median_ms": {
                    name: round(statistics.median(values), 3) for name, values in samples.items()
                },
                "samples_ms": samples,
            }
        )
    return {
        "date_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "repeats": repeats,
        "sessions_per_sample": 1,
        "note": "每次重复使用新临时日志；读取测量在追加一条消息后进行。无模型调用或耗时门槛。",
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="测量临时 JSONL 会话的全量重放耗时")
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 10000])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1 or any(count < 1 for count in args.sizes):
        parser.error("消息数量与重复次数必须大于零")
    print(
        json.dumps(asyncio.run(benchmark(args.sizes, args.repeats)), ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
