"""Tests for openjarvis.agents.access — per-agent data-source allowlist (AG-9)."""

from __future__ import annotations

import pytest

from openjarvis.agents.access import (
    DataSourceRefused,
    allowed_data_sources,
    enforce_data_source,
    is_data_source_allowed,
)


class TestAllowedDataSources:
    def test_none_config_returns_empty_list(self) -> None:
        assert allowed_data_sources(None) == []

    def test_empty_config_returns_empty_list(self) -> None:
        assert allowed_data_sources({}) == []

    def test_missing_key_returns_empty_list(self) -> None:
        assert allowed_data_sources({"name": "agent"}) == []

    def test_explicit_list_preserved(self) -> None:
        assert allowed_data_sources({"allowed_data_sources": ["gmail", "gdrive"]}) == [
            "gmail",
            "gdrive",
        ]

    def test_non_list_value_returns_empty(self) -> None:
        # Defensive: a malformed config value is treated as default-deny.
        assert allowed_data_sources({"allowed_data_sources": "gmail"}) == []
        assert allowed_data_sources({"allowed_data_sources": 5}) == []

    def test_non_string_entries_coerced(self) -> None:
        # A list with mixed types still produces a list of strings.
        assert allowed_data_sources({"allowed_data_sources": ["gmail", 123]}) == [
            "gmail",
            "123",
        ]


class TestIsDataSourceAllowed:
    def test_empty_list_denies_all(self) -> None:
        assert is_data_source_allowed({"allowed_data_sources": []}, "gmail") is False

    def test_explicit_grant_allows(self) -> None:
        cfg = {"allowed_data_sources": ["gmail", "gdrive"]}
        assert is_data_source_allowed(cfg, "gmail") is True
        assert is_data_source_allowed(cfg, "gdrive") is True

    def test_unlisted_source_denied(self) -> None:
        cfg = {"allowed_data_sources": ["gmail"]}
        assert is_data_source_allowed(cfg, "gdrive") is False

    def test_wildcard_grants_everything(self) -> None:
        cfg = {"allowed_data_sources": ["*"]}
        assert is_data_source_allowed(cfg, "gmail") is True
        assert is_data_source_allowed(cfg, "anything") is True

    def test_wildcard_alongside_explicit_list_still_grants_all(self) -> None:
        cfg = {"allowed_data_sources": ["gmail", "*"]}
        assert is_data_source_allowed(cfg, "notion") is True

    def test_none_config_denies(self) -> None:
        assert is_data_source_allowed(None, "gmail") is False


class TestEnforceDataSource:
    def test_allowed_no_raise(self) -> None:
        cfg = {"allowed_data_sources": ["gmail"]}
        enforce_data_source(cfg, "gmail")  # should not raise

    def test_disallowed_raises(self) -> None:
        cfg = {"allowed_data_sources": ["gmail"]}
        with pytest.raises(DataSourceRefused):
            enforce_data_source(cfg, "gdrive")

    def test_default_deny_raises(self) -> None:
        with pytest.raises(DataSourceRefused):
            enforce_data_source({}, "gmail")

    def test_wildcard_no_raise(self) -> None:
        cfg = {"allowed_data_sources": ["*"]}
        enforce_data_source(cfg, "any-connector")  # should not raise

    def test_exception_carries_agent_and_source(self) -> None:
        with pytest.raises(DataSourceRefused) as info:
            enforce_data_source({}, "gmail", agent_id="a-123")
        assert info.value.agent_id == "a-123"
        assert info.value.source_id == "gmail"
        assert "a-123" in str(info.value)
        assert "gmail" in str(info.value)

    def test_exception_without_agent_id(self) -> None:
        with pytest.raises(DataSourceRefused) as info:
            enforce_data_source({}, "gmail")
        assert info.value.agent_id is None
        assert info.value.source_id == "gmail"
        assert "gmail" in str(info.value)
