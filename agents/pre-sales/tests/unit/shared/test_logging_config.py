"""Contract tests for the verbose SOW-logging toggle.

The env var ``SOW_VERBOSE_LOGGING`` flips three layers of telemetry
on/off (manifest snapshot, SOW snapshots per round, op payload content).
The default — verbose OFF — keeps production logs lean; debug sessions
flip it on for the full forensic trail.

The helper reads the env var on every call so a developer toggling the
var between test invocations does not need a process restart.
"""
from __future__ import annotations

import pytest

from app.shared.logging_config import is_verbose_sow_logging


class TestIsVerboseSowLogging:
    def test_default_unset_is_false(self, monkeypatch):
        monkeypatch.delenv('SOW_VERBOSE_LOGGING', raising=False)
        assert is_verbose_sow_logging() is False

    @pytest.mark.parametrize('value', ['1', 'true', 'TRUE', 'yes', 'Yes', 'on', 'ON'])
    def test_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv('SOW_VERBOSE_LOGGING', value)
        assert is_verbose_sow_logging() is True

    @pytest.mark.parametrize('value', ['0', 'false', 'FALSE', 'no', 'off', '', '  '])
    def test_falsy_values_keep_off(self, monkeypatch, value):
        monkeypatch.setenv('SOW_VERBOSE_LOGGING', value)
        assert is_verbose_sow_logging() is False

    def test_whitespace_is_stripped(self, monkeypatch):
        monkeypatch.setenv('SOW_VERBOSE_LOGGING', '  1  ')
        assert is_verbose_sow_logging() is True

    def test_arbitrary_strings_are_falsy(self, monkeypatch):
        """Defensive: only the documented truthy tokens enable verbose
        mode. Anything else stays off so a typo in env config doesn't
        silently dump 200KB of telemetry per session."""
        monkeypatch.setenv('SOW_VERBOSE_LOGGING', 'maybe')
        assert is_verbose_sow_logging() is False
