import asyncio

from camel_harness.backend.approval import WebApproval
from camel_harness.tools import PermissionRequest
from camel_harness.types import PermissionMode


async def test_web_approval_can_be_resolved_from_pending_queue() -> None:
    """浏览器审批应挂起工具调用，并在用户决定后恢复。"""

    approval = WebApproval(timeout_seconds=1)
    request = PermissionRequest(
        "write_file", frozenset({"write"}), "write", {"path": "a"}, PermissionMode.DEFAULT
    )
    task = asyncio.create_task(approval.approve(request, "需要修改工作区"))
    await asyncio.sleep(0)

    pending = await approval.list_pending()
    assert len(pending) == 1
    assert pending[0].tool_name == "write_file"
    assert await approval.resolve(pending[0].request_id, True)
    assert await task
    assert await approval.list_pending() == ()
