"""Tests for openjarvis.channels.sender_filter — MC-4."""

from __future__ import annotations

import pytest

from openjarvis.channels.sender_filter import (
    SenderRefused,
    allowed_senders,
    enforce_sender,
    is_sender_allowed,
)


class TestAllowedSenders:
    def test_none_config_returns_empty(self) -> None:
        assert allowed_senders(None) == []

    def test_empty_config_returns_empty(self) -> None:
        assert allowed_senders({}) == []

    def test_missing_key_returns_empty(self) -> None:
        assert allowed_senders({"channel": "slack"}) == []

    def test_explicit_list_preserved(self) -> None:
        assert allowed_senders({"allowed_senders": ["U123", "U456"]}) == [
            "U123",
            "U456",
        ]

    def test_non_list_value_returns_empty(self) -> None:
        # Defensive: a malformed config value is treated as no restriction.
        assert allowed_senders({"allowed_senders": "U123"}) == []
        assert allowed_senders({"allowed_senders": 5}) == []

    def test_strips_empty_strings(self) -> None:
        # Empty strings are useless as IDs and are dropped silently.
        assert allowed_senders({"allowed_senders": ["U123", "", "U456"]}) == [
            "U123",
            "U456",
        ]

    def test_strips_none_entries(self) -> None:
        assert allowed_senders({"allowed_senders": ["U123", None, "U456"]}) == [
            "U123",
            "U456",
        ]

    def test_coerces_non_string_entries(self) -> None:
        # Telegram chat IDs are numeric; coerce to string.
        assert allowed_senders({"allowed_senders": [12345, "+15551234567"]}) == [
            "12345",
            "+15551234567",
        ]


class TestIsSenderAllowed:
    def test_empty_list_allows_all(self) -> None:
        # Empty allowlist == no restriction; this is the existing default.
        assert is_sender_allowed({"allowed_senders": []}, "anyone") is True

    def test_no_config_allows_all(self) -> None:
        assert is_sender_allowed(None, "U123") is True
        assert is_sender_allowed({}, "U123") is True

    def test_explicit_member_allowed(self) -> None:
        cfg = {"allowed_senders": ["U123", "U456"]}
        assert is_sender_allowed(cfg, "U123") is True
        assert is_sender_allowed(cfg, "U456") is True

    def test_non_member_denied_when_list_set(self) -> None:
        cfg = {"allowed_senders": ["U123"]}
        assert is_sender_allowed(cfg, "U999") is False

    def test_no_implicit_wildcard(self) -> None:
        # Unlike AG-9, sender_filter does NOT honor "*" as a wildcard.
        # The restriction model is "list a sender or don't list it" —
        # adding a literal "*" entry just lets a sender named exactly
        # "*" through.
        cfg = {"allowed_senders": ["*"]}
        assert is_sender_allowed(cfg, "U123") is False
        assert is_sender_allowed(cfg, "*") is True


class TestEnforceSender:
    def test_allowed_no_raise(self) -> None:
        cfg = {"allowed_senders": ["U123"]}
        enforce_sender(cfg, "U123")  # should not raise

    def test_disallowed_raises(self) -> None:
        cfg = {"allowed_senders": ["U123"]}
        with pytest.raises(SenderRefused):
            enforce_sender(cfg, "U999")

    def test_empty_list_no_raise(self) -> None:
        # Empty allowlist == allow all.
        enforce_sender({"allowed_senders": []}, "U999")
        enforce_sender({}, "U999")
        enforce_sender(None, "U999")

    def test_exception_carries_context(self) -> None:
        cfg = {"allowed_senders": ["U123"]}
        with pytest.raises(SenderRefused) as info:
            enforce_sender(cfg, "U999", agent_id="a-1", channel_type="slack")
        exc = info.value
        assert exc.sender_id == "U999"
        assert exc.agent_id == "a-1"
        assert exc.channel_type == "slack"
        msg = str(exc)
        assert "U999" in msg
        assert "slack" in msg
        assert "a-1" in msg

    def test_exception_without_optional_context(self) -> None:
        with pytest.raises(SenderRefused) as info:
            enforce_sender({"allowed_senders": ["U123"]}, "U999")
        exc = info.value
        assert exc.sender_id == "U999"
        assert exc.agent_id is None
        assert exc.channel_type is None
        # The message still mentions the offending sender.
        assert "U999" in str(exc)
