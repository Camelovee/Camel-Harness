from pathlib import Path

import typer
from typer.testing import CliRunner

from camel_cli.main import main


def _app() -> typer.Typer:
    app = typer.Typer()
    app.command()(main)
    return app


def test_latest_session_requires_existing_workspace_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    sessions = tmp_path / ".camel" / "sessions"
    result = CliRunner().invoke(
        _app(),
        ["--workspace", str(tmp_path), "--latest-session", "测试"],
    )
    assert result.exit_code == 2
    assert "没有可选择的历史会话" in result.output
    assert not list(sessions.glob("**/session.jsonl"))


def test_latest_session_conflicts_with_explicit_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = CliRunner().invoke(
        _app(),
        ["--session", "known", "--latest-session", "测试"],
    )
    assert result.exit_code == 2
    assert "不能同时使用" in result.output
