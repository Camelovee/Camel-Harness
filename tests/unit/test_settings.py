import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from camel_harness.agent.config import AgentSettings, ModelProfile


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelProfile(id="a", name="测试", provider="unknown", model="m")  # type: ignore[arg-type]


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
                "models": [
                    {
                        "id": "main",
                        "name": "主模型",
                        "provider": "openai",
                        "model": "json-model",
                        "api_key": "json-key",
                    }
                ],
                "default_model_id": "main",
                "session_dir": "custom/sessions",
            }
        ),
        encoding="utf-8",
    )

    settings = AgentSettings.load()
    resolved = settings.resolve_model(None)
    assert resolved.provider == "openai"
    assert resolved.model == "json-model"
    assert resolved.api_key == "json-key"
    assert settings.resolved_session_dir() == tmp_path / "custom" / "sessions"
    assert "json-key" not in repr(settings)
    assert "json-key" not in repr(settings.models[0])


def test_missing_json_uses_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / ".camel" / "config.json"
    settings = AgentSettings.load(path)
    assert settings.models == []
    assert settings.default_model_id == ""
    assert settings.resolved_session_dir() == tmp_path / ".camel" / "sessions"
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_concurrent_startup_creates_one_complete_config(tmp_path: Path) -> None:
    path = tmp_path / "settings" / "config.json"
    with ThreadPoolExecutor(max_workers=8) as pool:
        settings = list(pool.map(lambda _: AgentSettings.load(path), range(8)))
    assert all(item.default_model_id == "" for item in settings)
    assert list(path.parent.iterdir()) == [path]


def test_startup_preserves_existing_config(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    original = json.dumps(
        {
            "models": [
                {
                    "id": "a",
                    "name": "主模型",
                    "provider": "openai",
                    "model": "custom-model",
                    "base_url": None,
                    "api_key": "private-key",
                }
            ],
            "default_model_id": "a",
        },
        ensure_ascii=False,
    )
    path.write_text(original, encoding="utf-8")
    assert AgentSettings.load(path).models[0].model == "custom-model"
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "content",
    [
        '["a", "b"]',
        '{"models":',
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
    path.write_text(
        '{"models":[{"id":"a","name":"n","provider":"openai","model":"m","api_key":["private-secret"]}]}',
        encoding="utf-8",
    )
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


def test_resolve_model_errors_are_actionable(tmp_path: Path) -> None:
    settings = AgentSettings()
    with pytest.raises(ValueError, match="请先"):
        settings.resolve_model(None)
    settings.models.append(ModelProfile(id="a", name="无密钥", provider="openai", model="m"))
    with pytest.raises(ValueError, match="不存在"):
        settings.resolve_model("missing")
    with pytest.raises(ValueError, match="缺少 API Key"):
        settings.resolve_model("a")


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
