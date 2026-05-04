"""Unit tests for ``app.config``.

``AgentConfig`` is a pydantic_settings BaseSettings — we exercise env-var
loading, validation ranges, and the ADC fallback path without hitting real
GCP.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.config import AgentConfig


@pytest.fixture
def clean_env(monkeypatch):
    """Strip every env var that could leak into AgentConfig."""
    for var in list(os.environ):
        if var.startswith(('AGENT_', 'GOOGLE_')):
            monkeypatch.delenv(var, raising=False)
    return monkeypatch


class TestDefaults:
    def test_sensible_defaults(self, clean_env):
        cfg = AgentConfig(_env_file=None)
        assert cfg.LOCATION == 'global'
        assert cfg.GOOGLE_GENAI_USE_VERTEXAI is True
        assert cfg.COMPANY_NAME == 'GFT Technologies'
        assert cfg.TEMPERATURE == 0.2
        assert cfg.THINKING_BUDGET == 2048
        assert cfg.MAX_RETRIES == 3
        assert cfg.LOG_LEVEL == 'INFO'
        assert cfg.LOG_JSON is True

    def test_gemini_model_default(self, monkeypatch):
        monkeypatch.delenv('AGENT_GEMINI_MODEL', raising=False)
        cfg = AgentConfig(_env_file=None)
        assert 'gemini' in cfg.GEMINI_MODEL.lower()


class TestValidation:
    @pytest.mark.parametrize(
        'field,bad_value',
        [
            ('TEMPERATURE', -0.1),
            ('TEMPERATURE', 2.1),
            ('THINKING_BUDGET', -1),
            ('THINKING_BUDGET', 99999),
            ('MAX_RETRIES', 0),
            ('MAX_RETRIES', 21),
        ],
    )
    def test_out_of_range_rejected(self, field, bad_value):
        with pytest.raises(ValidationError):
            AgentConfig(_env_file=None, **{field: bad_value})

    @pytest.mark.parametrize('value', [0.0, 0.5, 1.0, 1.5, 2.0])
    def test_temperature_accepts_range(self, value):
        cfg = AgentConfig(_env_file=None, TEMPERATURE=value)
        assert cfg.TEMPERATURE == value

    @pytest.mark.parametrize('value', [0, 1024, 24576])
    def test_thinking_budget_accepts_range(self, value):
        cfg = AgentConfig(_env_file=None, THINKING_BUDGET=value)
        assert cfg.THINKING_BUDGET == value

    @pytest.mark.parametrize('value', [1, 3, 20])
    def test_max_retries_accepts_range(self, value):
        cfg = AgentConfig(_env_file=None, MAX_RETRIES=value)
        assert cfg.MAX_RETRIES == value


class TestEnvVarLoading:
    def test_agent_prefix_respected(self, monkeypatch):
        monkeypatch.setenv('AGENT_COMPANY_NAME', 'Acme')
        cfg = AgentConfig(_env_file=None)
        assert cfg.COMPANY_NAME == 'Acme'

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv('agent_company_name', 'CaseInsensitive')
        cfg = AgentConfig(_env_file=None)
        assert cfg.COMPANY_NAME == 'CaseInsensitive'

    def test_project_id_uses_google_cloud_project_alias(self, monkeypatch):
        monkeypatch.setenv('GOOGLE_CLOUD_PROJECT', 'my-project-123')
        cfg = AgentConfig(_env_file=None)
        assert cfg.PROJECT_ID == 'my-project-123'

    def test_location_uses_google_cloud_location_alias(self, monkeypatch):
        monkeypatch.setenv('GOOGLE_CLOUD_LOCATION', 'us-east1')
        cfg = AgentConfig(_env_file=None)
        assert cfg.LOCATION == 'us-east1'

    def test_extra_env_vars_ignored(self, monkeypatch):
        """extra='ignore' → unknown AGENT_* vars don't fail."""
        monkeypatch.setenv('AGENT_SOMETHING_NEW', 'value')
        # Must not raise
        AgentConfig(_env_file=None)

    def test_log_level_from_env(self, monkeypatch):
        monkeypatch.setenv('AGENT_LOG_LEVEL', 'DEBUG')
        cfg = AgentConfig(_env_file=None)
        assert cfg.LOG_LEVEL == 'DEBUG'

    def test_log_json_false_from_env(self, monkeypatch):
        monkeypatch.setenv('AGENT_LOG_JSON', 'false')
        cfg = AgentConfig(_env_file=None)
        assert cfg.LOG_JSON is False


class TestResolveProjectId:
    """Direct unit tests of resolve_project_id — bypass env-var loading.

    pydantic-settings can still pick up values from .env via aliases even
    when ``_env_file=None`` is passed, which makes test-time isolation of
    ``PROJECT_ID`` unreliable. These tests operate on the in-memory attribute
    directly — the env-loading path is covered in TestEnvVarLoading.
    """

    def test_explicit_project_id_wins(self, clean_env):
        cfg = AgentConfig(_env_file=None)
        cfg.PROJECT_ID = 'explicit-project'
        assert cfg.resolve_project_id() == 'explicit-project'

    def test_falls_back_to_adc_when_empty(self, clean_env):
        cfg = AgentConfig(_env_file=None)
        cfg.PROJECT_ID = ''
        with patch(
            'app.config.google.auth.default',
            return_value=(None, 'adc-project'),
        ):
            assert cfg.resolve_project_id() == 'adc-project'

    def test_returns_empty_when_adc_none(self, clean_env):
        cfg = AgentConfig(_env_file=None)
        cfg.PROJECT_ID = ''
        with patch(
            'app.config.google.auth.default', return_value=(None, None)
        ):
            assert cfg.resolve_project_id() == ''
