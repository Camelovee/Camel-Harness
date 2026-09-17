import pytest
from pydantic import ValidationError

from camel_harness.harness.config import HarnessSettings


def test_anthropic_is_default_provider() -> None:
    settings = HarnessSettings(_env_file=None)

    assert settings.provider == "anthropic"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        HarnessSettings(provider="unknown", _env_file=None)  # type: ignore[arg-type]
