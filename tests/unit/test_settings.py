import pytest
from pydantic import ValidationError

from camel_harness.agent.config import AgentSettings


def test_anthropic_is_default_provider() -> None:
    settings = AgentSettings(_env_file=None)

    assert settings.provider == "anthropic"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentSettings(provider="unknown", _env_file=None)  # type: ignore[arg-type]
