import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from camel_harness.agent.config import AgentSettings


def test_anthropic_is_default_provider() -> None:
    settings = AgentSettings()

    assert settings.provider == "anthropic"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentSettings(provider="unknown")  # type: ignore[arg-type]


def test_yaml_settings_ignore_environment_and_dotenv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAMEL_MODEL", "env-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    (tmp_path / ".env").write_text("CAMEL_MODEL=dotenv-model\n", encoding="utf-8")
    config = tmp_path / ".camel" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "provider: openai\nmodel: yaml-model\nopenai_api_key: yaml-key\n"
        "session_dir: custom/sessions\n",
        encoding="utf-8",
    )

    settings = AgentSettings.load()
    assert settings.provider == "openai"
    assert settings.model == "yaml-model"
    assert settings.openai_api_key == "yaml-key"
    assert settings.anthropic_api_key is None
    assert settings.resolved_session_dir() == tmp_path / "custom" / "sessions"
    assert "yaml-key" not in repr(settings)


def test_missing_yaml_uses_defaults(tmp_path: Path) -> None:
    path = tmp_path / ".camel" / "config.yaml"
    assert AgentSettings.load(path).provider == "anthropic"
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert AgentSettings.load(path).anthropic_api_key is None


def test_concurrent_startup_creates_one_complete_config(tmp_path: Path) -> None:
    path = tmp_path / "settings" / "config.yaml"
    with ThreadPoolExecutor(max_workers=8) as pool:
        settings = list(pool.map(lambda _: AgentSettings.load(path), range(8)))
    assert all(item.provider == "anthropic" for item in settings)
    assert list(path.parent.iterdir()) == [path]


def test_startup_preserves_existing_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    original = "# 我的配置\nprovider: openai\nmodel: custom-model\nopenai_api_key: private-key\n"
    path.write_text(original, encoding="utf-8")
    assert AgentSettings.load(path).model == "custom-model"
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("content", ["[a, b]", "model: [", "unknown: true", "provider: invalid"])
def test_invalid_yaml_reports_configuration_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="配置"):
        AgentSettings.load(path)


def test_validation_error_does_not_expose_key(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("anthropic_api_key: [private-secret]\n", encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        AgentSettings.load(path)
    assert "private-secret" not in str(caught.value)
    assert caught.value.__cause__ is None
