import json
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


def test_json_settings_ignore_environment_and_dotenv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAMEL_MODEL", "env-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    (tmp_path / ".env").write_text("CAMEL_MODEL=dotenv-model\n", encoding="utf-8")
    config = tmp_path / ".camel" / "config.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "provider": "openai",
                "model": "json-model",
                "openai_api_key": "json-key",
                "session_dir": "custom/sessions",
            }
        ),
        encoding="utf-8",
    )

    settings = AgentSettings.load()
    assert settings.provider == "openai"
    assert settings.model == "json-model"
    assert settings.openai_api_key == "json-key"
    assert settings.anthropic_api_key is None
    assert settings.resolved_session_dir() == tmp_path / "custom" / "sessions"
    assert "json-key" not in repr(settings)


def test_missing_json_uses_defaults(tmp_path: Path) -> None:
    path = tmp_path / ".camel" / "config.json"
    assert AgentSettings.load(path).provider == "anthropic"
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert AgentSettings.load(path).anthropic_api_key is None


def test_concurrent_startup_creates_one_complete_config(tmp_path: Path) -> None:
    path = tmp_path / "settings" / "config.json"
    with ThreadPoolExecutor(max_workers=8) as pool:
        settings = list(pool.map(lambda _: AgentSettings.load(path), range(8)))
    assert all(item.provider == "anthropic" for item in settings)
    assert list(path.parent.iterdir()) == [path]


def test_startup_preserves_existing_config(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    original = '{"provider":"openai","model":"custom-model","openai_api_key":"private-key"}'
    path.write_text(original, encoding="utf-8")
    assert AgentSettings.load(path).model == "custom-model"
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "content",
    [
        '["a", "b"]',
        '{"model":',
        '{"unknown":true}',
        '{"provider":"invalid"}',
        "",
        "null",
        '{"max_compaction_tokens":2048}',
        '{"max_total_tokens":200000}',
    ],
)
def test_invalid_json_reports_configuration_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="配置"):
        AgentSettings.load(path)


def test_validation_error_does_not_expose_key(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"anthropic_api_key":["private-secret"]}', encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        AgentSettings.load(path)
    assert "private-secret" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_nested_compaction_is_independent_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"max_output_tokens": 100, "compaction": {"max_output_tokens": 8192}}),
        encoding="utf-8",
    )
    settings = AgentSettings.load(path)
    assert settings.max_output_tokens == 100
    assert settings.compaction.max_output_tokens == 8192
    path.write_text('{"compaction":{"retry_max_output_tokens":0}}', encoding="utf-8")
    with pytest.raises(ValueError, match="compaction.retry_max_output_tokens"):
        AgentSettings.load(path)


def test_one_time_migration_preserves_credentials_and_never_overwrites(tmp_path: Path) -> None:
    from scripts.migrate_config import migrate

    source = tmp_path / "config.yaml"
    target = tmp_path / "config.json"
    original = "model: old-model\nanthropic_api_key: private-secret\nmax_compaction_tokens: 2048\n"
    source.write_text(original, encoding="utf-8")
    migrate(source, target)
    settings = AgentSettings.load(target)
    assert settings.model == "old-model"
    assert settings.anthropic_api_key == "private-secret"
    assert settings.compaction.max_output_tokens == 8192
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        migrate(source, target)
    assert target.read_bytes() == before
    assert source.read_text(encoding="utf-8") == original
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.json", "config.yaml"]


@pytest.mark.parametrize("field", ["max_entries", "context_tokens"])
def test_memory_budget_validation_and_defaults(tmp_path: Path, field: str) -> None:
    path = tmp_path / "config.json"
    settings = AgentSettings.load(path)
    assert settings.memory.enabled
    assert settings.memory.max_entries == 5
    assert settings.memory.context_tokens == 2000
    path.write_text(json.dumps({"memory": {field: 0}}), encoding="utf-8")
    with pytest.raises(ValueError, match=f"memory.{field}"):
        AgentSettings.load(path)
    path.write_text('{"memory":{"enabled":false,"context_tokens":300}}', encoding="utf-8")
    assert not AgentSettings.load(path).memory.enabled
    assert AgentSettings.load(path).memory.context_tokens == 300
