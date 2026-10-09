import asyncio
import threading

import pytest

from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.llm import UserMessage


@pytest.mark.parametrize("branched", [False, True])
async def test_cancelled_append_waits_for_worker_before_releasing_session_lock(
    tmp_path, monkeypatch, branched
):
    storage = JsonlSessionStorage(tmp_path / "sessions")
    sessions = SessionManager(storage)
    await sessions.create("write", tmp_path, "写入取消")
    session = sessions.bind("write")
    if branched:
        await session.branch(None)
    started, release = threading.Event(), threading.Event()
    append = storage._append_sync

    def gated_append(*args):
        started.set()
        release.wait(timeout=5)
        return append(*args)

    monkeypatch.setattr(storage, "_append_sync", gated_append)
    write = asyncio.create_task(session.append_message(UserMessage("完整消息")))
    while not started.is_set():
        await asyncio.sleep(0)
    write.cancel()
    await asyncio.sleep(0)
    read = asyncio.create_task(sessions.bind("write").messages())
    await asyncio.sleep(0)
    assert not write.done()
    assert not read.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await write
    assert await read == (UserMessage("完整消息"),)
    assert await session.messages() == (UserMessage("完整消息"),)
