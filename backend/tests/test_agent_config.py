import os

import pytest
from pydantic import ValidationError

from app.core.config import AgentSettings


@pytest.fixture(autouse=True)
def isolated_agent_environment(monkeypatch):
    for name in list(os.environ):
        if name.upper().startswith('AGENT_') or name.upper() == 'DASHSCOPE_API_KEY':
            monkeypatch.delenv(name)


def test_real_agent_is_disabled_without_credentials_or_prices():
    config = AgentSettings(_env_file=None)
    assert config.enabled is False
    assert config.model == 'qwen-plus'
    assert config.budget_microyuan == 10_000_000
    assert config.api_key is None


def test_enabling_real_agent_requires_credentials_and_verified_pricing():
    with pytest.raises(ValidationError):
        AgentSettings(_env_file=None, enabled=True)


def test_environment_configuration_redacts_credentials(monkeypatch):
    monkeypatch.setenv('AGENT_ENABLED', 'true')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-only-not-a-real-secret')
    monkeypatch.setenv('AGENT_REGION_CONFIRMED', 'true')
    monkeypatch.setenv('AGENT_INPUT_MICROYUAN_PER_MILLION_TOKENS', '2000000')
    monkeypatch.setenv('AGENT_OUTPUT_MICROYUAN_PER_MILLION_TOKENS', '8000000')
    monkeypatch.setenv('AGENT_PRICE_VERSION', 'test-prices-only')
    config = AgentSettings(_env_file=None)
    assert config.enabled is True
    assert 'test-only-not-a-real-secret' not in repr(config)
    assert 'test-only-not-a-real-secret' not in config.model_dump_json()


@pytest.mark.parametrize('changes', [
    {'base_url': 'http://dashscope.aliyuncs.com/compatible-mode/v1'},
    {'base_url': 'https://example.invalid/compatible-mode/v1'},
    {'model': 'unapproved-model'},
    {'budget_microyuan': 10_000_001},
    {'budget_microyuan': 0},
    {'input_microyuan_per_million_tokens': 0},
])
def test_unapproved_access_and_invalid_budget_are_rejected(changes):
    with pytest.raises(ValidationError):
        AgentSettings(_env_file=None, **changes)
