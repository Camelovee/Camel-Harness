from pathlib import Path
from typing import Any, cast

from camel_harness.harness import Harness
from camel_harness.harness.session import SessionManager
from camel_harness.harness.session.jsonl import JsonlSessionStorage


async def _unused_workspace_picker() -> Path | None:
    return None


async def test_workspaces_are_projected_from_session_directories(tmp_path: Path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    await sessions.create("first-session", first_workspace, "第一个会话")
    await sessions.create("second-session", first_workspace, "第二个会话")
    await sessions.create("third-session", second_workspace, "第三个会话")

    harness = Harness(
        cast(Any, object()),
        sessions,
        cast(Any, object()),
        cast(Any, object()),
        _unused_workspace_picker,
    )
    result = await harness.list_workspaces()

    grouped = dict(result)
    assert {item.session_id for item in grouped[str(first_workspace.resolve())]} == {
        "first-session",
        "second-session",
    }
    assert [item.session_id for item in grouped[str(second_workspace.resolve())]] == [
        "third-session"
    ]


async def test_workspace_selection_uses_injected_platform_function(tmp_path: Path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    selected = tmp_path / "selected"

    async def select_workspace() -> Path | None:
        return selected

    harness = Harness(
        cast(Any, object()),
        sessions,
        cast(Any, object()),
        cast(Any, object()),
        select_workspace,
    )

    assert await harness.select_workspace() == selected
