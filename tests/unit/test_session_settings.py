from pathlib import Path

from camel_harness.agent.config import AgentSettings


def test_default_session_directory_is_global() -> None:
    settings = AgentSettings.model_construct()

    assert settings.resolved_session_dir() == (Path.home() / ".camel" / "sessions").resolve()


def test_relative_session_directory_is_resolved_from_home() -> None:
    settings = AgentSettings.model_construct(session_dir=Path("custom/sessions"))

    assert settings.resolved_session_dir() == (Path.home() / "custom" / "sessions").resolve()
