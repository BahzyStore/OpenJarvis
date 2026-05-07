"""Per-channel-binding inbound sender allowlist — MC-4.

Pure-function primitive for filtering inbound messages by sender ID.
Reads the allowlist from a channel binding's free-form ``config`` dict
under the key ``"allowed_senders"``. Designed to be called from any
post-routing inbound check site once the dispatch wiring is added.

**Empty allowlist = allow all.** Channel bindings have always permitted
all senders, so flipping to default-deny would silently break every
existing binding the moment any caller starts checking. Restriction is
opt-in: a binding only enforces a filter when its allowlist is non-empty.

Public surface
--------------

    allowed_senders(config)              -> list[str]
    is_sender_allowed(config, sender_id) -> bool
    enforce_sender(
        config, sender_id, *,
        agent_id=None, channel_type=None,
    ) -> None  (raises)
    SenderRefused                          exception

Sender-ID format varies per channel type (Slack: ``"U123ABC"``,
SendBlue: ``"+15551234567"``, Telegram: numeric chat ID, …); this
module is format-agnostic and only checks list membership. Channel-
specific format validation, if any, belongs in the channel adapter.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class SenderRefused(Exception):
    """Raised when an inbound sender is not in the binding's
    :data:`allowed_senders` list."""

    def __init__(
        self,
        sender_id: str,
        *,
        agent_id: Optional[str] = None,
        channel_type: Optional[str] = None,
    ) -> None:
        self.sender_id = sender_id
        self.agent_id = agent_id
        self.channel_type = channel_type
        parts = [f"sender {sender_id!r} is not in the binding's allowlist"]
        if channel_type:
            parts.append(f"channel={channel_type}")
        if agent_id:
            parts.append(f"agent={agent_id}")
        super().__init__("; ".join(parts))


def allowed_senders(config: Optional[Dict[str, Any]]) -> List[str]:
    """Return the binding's sender allowlist from its ``config``.

    Returns an empty list (which means "allow all" — restriction is
    opt-in) when ``config`` is ``None``, when the key is missing, or
    when the value is malformed.
    """
    if not config:
        return []
    raw = config.get("allowed_senders")
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if x is not None and str(x) != ""]


def is_sender_allowed(config: Optional[Dict[str, Any]], sender_id: str) -> bool:
    """Return ``True`` if *sender_id* is permitted by the binding.

    With an empty allowlist (the default), all senders are allowed.
    With a non-empty allowlist, only senders whose IDs appear in the
    list are allowed.
    """
    allowed = allowed_senders(config)
    if not allowed:
        return True
    return sender_id in allowed


def enforce_sender(
    config: Optional[Dict[str, Any]],
    sender_id: str,
    *,
    agent_id: Optional[str] = None,
    channel_type: Optional[str] = None,
) -> None:
    """Raise :class:`SenderRefused` if *sender_id* is not permitted by
    the binding's allowlist. No-op when the allowlist is empty (allow
    all) or when *sender_id* is in the list.

    The optional *agent_id* and *channel_type* are attached to the
    exception for debuggability — pass them from the caller (typically
    the inbound dispatch in the channel bridge).
    """
    if not is_sender_allowed(config, sender_id):
        raise SenderRefused(
            sender_id,
            agent_id=agent_id,
            channel_type=channel_type,
        )


__all__ = [
    "SenderRefused",
    "allowed_senders",
    "enforce_sender",
    "is_sender_allowed",
]
