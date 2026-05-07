"""Tests for openjarvis.channels.slack_credentials — MC-2."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjarvis.channels.slack_credentials import (
    APP_TOKEN_PREFIX,
    BOT_TOKEN_PREFIX,
    SlackCredentialError,
    mask_token,
    validate_app_token,
    validate_bot_token,
    verify_credentials,
)


class TestPrefixes:
    def test_prefixes(self) -> None:
        assert BOT_TOKEN_PREFIX == "xoxb-"
        assert APP_TOKEN_PREFIX == "xapp-"


class TestValidateBotToken:
    def test_valid_token_no_raise(self) -> None:
        validate_bot_token("xoxb-test")
        validate_bot_token("xoxb-also-fake-token")

    def test_empty_raises(self) -> None:
        with pytest.raises(SlackCredentialError):
            validate_bot_token("")

    def test_wrong_prefix_app_raises(self) -> None:
        with pytest.raises(SlackCredentialError) as info:
            validate_bot_token("xapp-not-a-bot-token")
        assert "xoxb-" in str(info.value)

    def test_wrong_prefix_oauth_raises(self) -> None:
        # xoxa- and xoxp- are user / legacy tokens, not bot tokens.
        with pytest.raises(SlackCredentialError):
            validate_bot_token("xoxa-deprecated")
        with pytest.raises(SlackCredentialError):
            validate_bot_token("xoxp-personal")

    def test_no_prefix_raises(self) -> None:
        with pytest.raises(SlackCredentialError):
            validate_bot_token("just-a-bare-string")

    def test_error_message_masks_token(self) -> None:
        # Error must not leak the middle portion of a bad token.
        bad = "xoxa-FAKE-MIDDLEPART-XYZZ"
        with pytest.raises(SlackCredentialError) as info:
            validate_bot_token(bad)
        assert "MIDDLEPART" not in str(info.value)


class TestValidateAppToken:
    def test_valid_token_no_raise(self) -> None:
        validate_app_token("xapp-fake-test-token")

    def test_empty_raises(self) -> None:
        with pytest.raises(SlackCredentialError):
            validate_app_token("")

    def test_bot_token_rejected(self) -> None:
        # A bot token must NOT pass the app-token validator.
        with pytest.raises(SlackCredentialError) as info:
            validate_app_token("xoxb-this-is-a-bot")
        assert "xapp-" in str(info.value)

    def test_no_prefix_raises(self) -> None:
        with pytest.raises(SlackCredentialError):
            validate_app_token("nope")


class TestMaskToken:
    def test_long_token_preserves_prefix_and_last4(self) -> None:
        masked = mask_token("xoxb-FAKEMIDDLE-PARTXX-zzzz")
        # First 5 (prefix) and last 4 are visible; middle is redacted.
        assert masked.startswith("xoxb-")
        assert masked.endswith("zzzz")
        assert "***" in masked
        # The middle of the original token must NOT appear in the mask.
        assert "FAKEMIDDLE" not in masked
        assert "PARTXX" not in masked

    def test_empty_token(self) -> None:
        assert mask_token("") == "(empty)"

    def test_short_token_fully_redacted(self) -> None:
        assert mask_token("xo") == "***"
        # 8 chars or fewer → "***"
        assert mask_token("xoxb-ab") == "***"

    def test_exactly_nine_chars_uses_normal_format(self) -> None:
        masked = mask_token("xoxb-abcd")
        assert masked.startswith("xoxb-")
        assert masked.endswith("abcd")


# ---------------------------------------------------------------------------
# verify_credentials — uses an injected mock http_client to avoid real network
# (renamed from test_credentials to avoid pytest collecting it as a test)
# ---------------------------------------------------------------------------


def _make_mock_client(
    auth_test: dict,
    connections_open: dict,
) -> MagicMock:
    """Return a MagicMock client that returns *auth_test* JSON for the
    auth.test endpoint and *connections_open* JSON for the
    apps.connections.open endpoint."""
    client = MagicMock()
    auth_resp = MagicMock()
    auth_resp.json.return_value = auth_test
    open_resp = MagicMock()
    open_resp.json.return_value = connections_open

    def post(url: str, **_kwargs):
        if "auth.test" in url:
            return auth_resp
        if "connections.open" in url:
            return open_resp
        raise AssertionError(f"unexpected URL: {url}")

    client.post.side_effect = post
    return client


class TestVerifyCredentials:
    def test_both_ok_returns_team_info(self) -> None:
        client = _make_mock_client(
            auth_test={
                "ok": True,
                "team_id": "T123",
                "team": "Acme Inc",
                "user_id": "U456",
                "bot_id": "B789",
            },
            connections_open={"ok": True, "url": "wss://..."},
        )
        ok, info = verify_credentials("xoxb-bot", "xapp-app", http_client=client)
        assert ok is True
        assert info["team_id"] == "T123"
        assert info["team_name"] == "Acme Inc"
        assert info["bot_user_id"] == "U456"
        assert info["bot_id"] == "B789"
        assert info["socket_url_present"] is True

    def test_auth_test_failure_returns_reason(self) -> None:
        client = _make_mock_client(
            auth_test={"ok": False, "error": "invalid_auth"},
            connections_open={"ok": True, "url": "wss://..."},
        )
        ok, reason = verify_credentials("xoxb-bad", "xapp-app", http_client=client)
        assert ok is False
        assert "invalid_auth" in reason

    def test_apps_connections_open_failure_returns_reason(self) -> None:
        client = _make_mock_client(
            auth_test={
                "ok": True,
                "team_id": "T",
                "team": "X",
                "user_id": "U",
                "bot_id": "B",
            },
            connections_open={"ok": False, "error": "not_allowed_token_type"},
        )
        ok, reason = verify_credentials("xoxb-good", "xapp-bad", http_client=client)
        assert ok is False
        assert "not_allowed_token_type" in reason
        assert "apps.connections.open" in reason

    def test_socket_url_missing_still_ok(self) -> None:
        """If Slack returns ok but no url, we still report success but
        with socket_url_present=False (rare but defensible)."""
        client = _make_mock_client(
            auth_test={
                "ok": True,
                "team_id": "T",
                "team": "X",
                "user_id": "U",
                "bot_id": "B",
            },
            connections_open={"ok": True},  # no url field
        )
        ok, info = verify_credentials("xoxb-good", "xapp-good", http_client=client)
        assert ok is True
        assert info["socket_url_present"] is False

    def test_transport_error_returns_reason(self) -> None:
        client = MagicMock()
        client.post.side_effect = RuntimeError("connection reset")
        ok, reason = verify_credentials("xoxb-good", "xapp-good", http_client=client)
        assert ok is False
        assert "auth.test" in reason
        assert "connection reset" in reason

    def test_malformed_json_returns_reason(self) -> None:
        client = MagicMock()
        bad_resp = MagicMock()
        bad_resp.json.side_effect = ValueError("not JSON")
        client.post.return_value = bad_resp
        ok, reason = verify_credentials("xoxb-good", "xapp-good", http_client=client)
        assert ok is False
