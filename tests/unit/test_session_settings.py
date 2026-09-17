from pathlib import Path

from camel_harness.harness.config import HarnessSettings


def test_default_session_directory_is_global() -> None:
    settings = HarnessSettings.model_construct()

    assert settings.resolved_session_dir() == (Path.home() / ".camel" / "sessions").resolve()


def test_relative_session_directory_is_resolved_from_home() -> None:
    settings = HarnessSettings.model_construct(session_dir=Path("custom/sessions"))

    assert settings.resolved_session_dir() == (Path.home() / "custom" / "sessions").resolve()
