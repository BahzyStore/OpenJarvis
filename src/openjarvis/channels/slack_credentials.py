"""Slack credential validation, masking, and connectivity helpers — MC-2.

Pure-function helpers that any caller (``SlackChannel``, ``slack_daemon``,
future OAuth flows, CLI tools) can use to validate Slack credentials
before trusting them. The network-touching :func:`verify_credentials`
helper is opt-in — none of the validators here perform HTTP calls.

Public surface
--------------

* :data:`BOT_TOKEN_PREFIX` / :data:`APP_TOKEN_PREFIX` — the canonical
  prefixes Slack issues. Slack docs:
  https://api.slack.com/authentication/token-types
* :class:`SlackCredentialError` — raised by the validators on bad shapes.
* :func:`validate_bot_token` — non-empty + ``xoxb-`` prefix.
* :func:`validate_app_token` — non-empty + ``xapp-`` prefix.
* :func:`mask_token` — returns ``"<prefix>***...***<last4>"`` for safe
  logging; never round-trips back to the original token.
* :func:`verify_credentials` — calls Slack's ``auth.test`` and
  ``apps.connections.open`` to verify bot- and app-level tokens are
  live and return the expected workspace metadata. Optional injected
  ``http_client`` makes this trivially mockable in tests.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

#: Slack bot user OAuth token prefix.
BOT_TOKEN_PREFIX: str = "xoxb-"

#: Slack app-level token prefix (used by Socket Mode).
APP_TOKEN_PREFIX: str = "xapp-"

_AUTH_TEST_URL: str = "https://slack.com/api/auth.test"
_CONNECTIONS_OPEN_URL: str = "https://slack.com/api/apps.connections.open"


class SlackCredentialError(ValueError):
    """Raised when a Slack token fails shape validation.

    Inherits from :class:`ValueError` so callers that catch ``ValueError``
    (a common pattern in argument-parsing code paths) keep working.
    """


def validate_bot_token(token: str) -> None:
    """Raise :class:`SlackCredentialError` if *token* is not a plausible
    Slack bot token (non-empty, ``xoxb-`` prefix). No-op on success."""
    if not token:
        raise SlackCredentialError("Slack bot token is empty")
    if not token.startswith(BOT_TOKEN_PREFIX):
        raise SlackCredentialError(
            f"Slack bot token must start with {BOT_TOKEN_PREFIX!r}; "
            f"got {mask_token(token)!r}"
        )


def validate_app_token(token: str) -> None:
    """Raise :class:`SlackCredentialError` if *token* is not a plausible
    Slack app-level token (non-empty, ``xapp-`` prefix). No-op on success."""
    if not token:
        raise SlackCredentialError("Slack app token is empty")
    if not token.startswith(APP_TOKEN_PREFIX):
        raise SlackCredentialError(
            f"Slack app token must start with {APP_TOKEN_PREFIX!r}; "
            f"got {mask_token(token)!r}"
        )


def mask_token(token: str) -> str:
    """Return a masked representation of *token* safe for logs.

    Preserves the first 5 characters (typically a Slack prefix like
    ``"xoxb-"``) and the last 4 characters; everything between is
    redacted. Tokens shorter than 9 characters return ``"***"`` to
    avoid leaking shape; empty tokens return ``"(empty)"``.
    """
    if not token:
        return "(empty)"
    if len(token) < 9:
        return "***"
    return f"{token[:5]}***...***{token[-4:]}"


def verify_credentials(
    bot_token: str,
    app_token: str,
    *,
    http_client: Optional[Any] = None,
) -> Tuple[bool, Any]:
    """Verify *bot_token* and *app_token* by calling Slack APIs.

    Calls ``auth.test`` (validates the bot token + returns workspace
    metadata) followed by ``apps.connections.open`` (validates the
    app-level token + returns a Socket Mode WSS URL).

    Returns ``(True, info)`` where ``info`` is a dict containing
    ``team_id``, ``team_name``, ``bot_user_id``, ``bot_id``, and
    ``socket_url_present``. Returns ``(False, reason)`` with a
    human-readable string when either token is rejected by Slack or a
    transport error occurs.

    *http_client* may be any object exposing ``.post(url, headers=...)
    -> response_with_.json()``. When ``None``, a short-lived
    :class:`httpx.Client` is created and closed inside this function.
    """
    own_client = http_client is None
    if own_client:
        import httpx

        http_client = httpx.Client(timeout=10.0)
    try:
        # Step 1 — bot token via auth.test.
        try:
            resp = http_client.post(
                _AUTH_TEST_URL,
                headers={"Authorization": f"Bearer {bot_token}"},
            )
            data = resp.json()
        except Exception as exc:
            return False, f"auth.test request failed: {exc}"
        if not data.get("ok"):
            return False, (
                f"auth.test rejected bot token: {data.get('error', 'unknown error')}"
            )
        info: dict = {
            "team_id": data.get("team_id", ""),
            "team_name": data.get("team", ""),
            "bot_user_id": data.get("user_id", ""),
            "bot_id": data.get("bot_id", ""),
        }

        # Step 2 — app token via apps.connections.open.
        try:
            resp = http_client.post(
                _CONNECTIONS_OPEN_URL,
                headers={"Authorization": f"Bearer {app_token}"},
            )
            data = resp.json()
        except Exception as exc:
            return False, f"apps.connections.open request failed: {exc}"
        if not data.get("ok"):
            return False, (
                f"apps.connections.open rejected app token: "
                f"{data.get('error', 'unknown error')}"
            )
        info["socket_url_present"] = bool(data.get("url"))
        return True, info
    finally:
        if own_client:
            try:
                http_client.close()
            except Exception:
                pass


__all__ = [
    "APP_TOKEN_PREFIX",
    "BOT_TOKEN_PREFIX",
    "SlackCredentialError",
    "mask_token",
    "verify_credentials",
    "validate_app_token",
    "validate_bot_token",
]
