"""Per-agent data-source allowlist — AG-9.

Default-deny: a new agent's ``config["allowed_data_sources"]`` is an
empty list, which means the agent has no connector access until the
allowlist is populated via the ``/access`` route or by editing config
directly.

Wildcard ``"*"`` in the list grants access to every connector
registered in :class:`openjarvis.core.registry.ConnectorRegistry`.

Public surface
--------------

    allowed_data_sources(config)              -> list[str]
    is_data_source_allowed(config, source_id) -> bool
    enforce_data_source(config, source_id, *, agent_id=None) -> None
    DataSourceRefused                          exception

This module ships as a pure primitive. Wiring ``enforce_data_source``
into the tool layer (``tools/digest_collect.py``,
``tools/knowledge_search.py``, ``tools/knowledge_sql.py``, …) is
intentionally deferred to follow-up phases — each tool's integration is
a small, isolated change that's easier to review and revert
individually.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class DataSourceRefused(Exception):
    """Raised when an agent attempts to access a connector that is not
    in its :data:`allowed_data_sources` list."""

    def __init__(
        self,
        agent_id: Optional[str],
        source_id: str,
    ) -> None:
        self.agent_id = agent_id
        self.source_id = source_id
        if agent_id:
            super().__init__(
                f"Agent {agent_id} is not permitted to access data source {source_id!r}"
            )
        else:
            super().__init__(
                f"Data source {source_id!r} is not in the agent's allowlist"
            )


def allowed_data_sources(config: Optional[Dict[str, Any]]) -> List[str]:
    """Return the agent's data-source allowlist from its ``config``.

    Returns an empty list (default-deny) when ``config`` is ``None``,
    when the key is missing, or when the value is malformed.
    """
    if not config:
        return []
    raw = config.get("allowed_data_sources")
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw]


def is_data_source_allowed(config: Optional[Dict[str, Any]], source_id: str) -> bool:
    """Return ``True`` if *source_id* is in the agent's allowlist, or
    if the wildcard ``"*"`` is present (grants everything)."""
    allowed = allowed_data_sources(config)
    if "*" in allowed:
        return True
    return source_id in allowed


def enforce_data_source(
    config: Optional[Dict[str, Any]],
    source_id: str,
    *,
    agent_id: Optional[str] = None,
) -> None:
    """Raise :class:`DataSourceRefused` if *source_id* is not allowed.

    The optional *agent_id* is included in the exception message for
    debuggability; pass it from the tool/runner that holds the agent
    context. No-op (returns ``None``) when access is allowed.
    """
    if not is_data_source_allowed(config, source_id):
        raise DataSourceRefused(agent_id, source_id)


__all__ = [
    "DataSourceRefused",
    "allowed_data_sources",
    "enforce_data_source",
    "is_data_source_allowed",
]
