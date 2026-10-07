import json
from pathlib import Path

import pytest

from scripts.migrate_flat_sessions import migrate


def _legacy(root: Path, session_id: str, parent: str | None = None) -> Path:
    path = root / f"{session_id}.jsonl"
    header = {"type": "session", "id": session_id, "parentSessionId": parent}
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")
    return path


def test_migration_is_copy_only_and_idempotent(tmp_path: Path) -> None:
    parent = _legacy(tmp_path, "parent")
    child = _legacy(tmp_path, "child", "parent")
    preview = migrate(tmp_path)
    assert len(preview) == 2
    assert not (tmp_path / "parent").exists()

    migrate(tmp_path, apply=True)
    assert (tmp_path / "parent" / "session.jsonl").read_bytes() == parent.read_bytes()
    assert (
        tmp_path / "parent" / "subagents" / "child" / "session.jsonl"
    ).read_bytes() == child.read_bytes()
    assert len(migrate(tmp_path, apply=True)) == 2
    assert parent.exists() and child.exists()


def test_migration_rejects_conflicting_target(tmp_path: Path) -> None:
    _legacy(tmp_path, "session")
    target = tmp_path / "session" / "session.jsonl"
    target.parent.mkdir()
    target.write_text("different", encoding="utf-8")
    with pytest.raises(ValueError, match="目标内容冲突"):
        migrate(tmp_path, apply=True)
    assert target.read_text(encoding="utf-8") == "different"
