"""Tests for KnowledgeSQLTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.connectors.store import KnowledgeStore
from openjarvis.core.registry import ToolRegistry


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeStore:
    ks = KnowledgeStore(str(tmp_path / "test.db"))
    ks.store("Hello from Alice", source="imessage", author="Alice", doc_type="message")
    ks.store(
        "Hello from Alice again", source="imessage", author="Alice", doc_type="message"
    )
    ks.store("Meeting notes Q1", source="granola", author="Bob", doc_type="document")
    ks.store("Email about Spain trip", source="gmail", author="Carol", doc_type="email")
    return ks


def test_select_count(store: KnowledgeStore) -> None:
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    tool = KnowledgeSQLTool(store=store)
    result = tool.execute(query="SELECT COUNT(*) as total FROM knowledge_chunks")
    assert result.success
    assert "4" in result.content


def test_group_by_author(store: KnowledgeStore) -> None:
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    tool = KnowledgeSQLTool(store=store)
    result = tool.execute(
        query=(
            "SELECT author, COUNT(*) as n "
            "FROM knowledge_chunks "
            "GROUP BY author ORDER BY n DESC"
        )
    )
    assert result.success
    assert "Alice" in result.content
    assert "2" in result.content


def test_rejects_non_select(store: KnowledgeStore) -> None:
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    tool = KnowledgeSQLTool(store=store)
    result = tool.execute(query="DELETE FROM knowledge_chunks")
    assert not result.success
    assert "read-only" in result.content.lower() or "SELECT" in result.content


def test_rejects_drop(store: KnowledgeStore) -> None:
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    tool = KnowledgeSQLTool(store=store)
    result = tool.execute(query="DROP TABLE knowledge_chunks")
    assert not result.success


def test_handles_bad_sql(store: KnowledgeStore) -> None:
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    tool = KnowledgeSQLTool(store=store)
    result = tool.execute(query="SELECT * FROM nonexistent_table")
    assert not result.success


def test_filter_by_source(store: KnowledgeStore) -> None:
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    tool = KnowledgeSQLTool(store=store)
    result = tool.execute(
        query="SELECT title, author FROM knowledge_chunks WHERE source = 'gmail'"
    )
    assert result.success
    assert "Carol" in result.content


def test_registered() -> None:
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    ToolRegistry.register_value("knowledge_sql", KnowledgeSQLTool)
    assert ToolRegistry.contains("knowledge_sql")


# ---------------------------------------------------------------------------
# AG-9: hard-deny raw SQL under a restricted per-agent allowlist
# ---------------------------------------------------------------------------


def test_restricted_allowlist_refuses_sql(store: KnowledgeStore) -> None:
    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    # Reset to discard MEMORY_STORE events emitted by the `store` fixture's
    # setup, which created the singleton with record_history=False.
    reset_event_bus()
    bus = get_event_bus(record_history=True)

    tool = KnowledgeSQLTool(store=store)
    result = tool.execute(
        query="SELECT COUNT(*) FROM knowledge_chunks",
        allowed_data_sources=["gmail"],
    )

    assert result.success is False
    assert "not permitted" in result.content.lower()
    assert (
        result.metadata["refused_reason"]
        == "raw_sql_disabled_under_restricted_allowlist"
    )

    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert len(refused) == 1
    assert refused[0].data["tool"] == "knowledge_sql"
    assert refused[0].data["allowed_data_sources"] == ["gmail"]
    assert refused[0].data["reason"] == "raw_sql_disabled_under_restricted_allowlist"
    assert refused[0].data["source_id"] is None


def test_wildcard_allows_sql(store: KnowledgeStore) -> None:
    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    reset_event_bus()
    bus = get_event_bus(record_history=True)

    tool = KnowledgeSQLTool(store=store)
    result = tool.execute(
        query="SELECT COUNT(*) as total FROM knowledge_chunks",
        allowed_data_sources=["*"],
    )

    assert result.success is True
    assert "4" in result.content

    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert refused == []


def test_empty_allowlist_refuses_sql(store: KnowledgeStore) -> None:
    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    reset_event_bus()
    bus = get_event_bus(record_history=True)

    tool = KnowledgeSQLTool(store=store)
    result = tool.execute(
        query="SELECT COUNT(*) FROM knowledge_chunks",
        allowed_data_sources=[],
    )

    # Empty list = default-deny (consistent with Phase 5 / Phase 9 / Phase 12).
    assert result.success is False
    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert len(refused) == 1
    assert refused[0].data["allowed_data_sources"] == []


def test_no_kwarg_unchanged_behavior(store: KnowledgeStore) -> None:
    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    reset_event_bus()
    bus = get_event_bus(record_history=True)

    tool = KnowledgeSQLTool(store=store)
    # No allowed_data_sources kwarg → legacy path, query runs.
    result = tool.execute(query="SELECT COUNT(*) as total FROM knowledge_chunks")

    assert result.success is True
    assert "4" in result.content

    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert refused == []


def test_refusal_includes_agent_id_via_dispatcher(store: KnowledgeStore) -> None:
    """End-to-end: ToolExecutor injects _agent_id, the hard-deny emit
    forwards it to the DATA_SOURCE_REFUSED payload."""
    import json as _json

    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.core.types import ToolCall
    from openjarvis.tools._stubs import ToolExecutor
    from openjarvis.tools.knowledge_sql import KnowledgeSQLTool

    reset_event_bus()
    bus = get_event_bus(record_history=True)

    tool = KnowledgeSQLTool(store=store)
    executor = ToolExecutor(
        [tool],
        agent_id="agent-sql-007",
        allowed_data_sources=["gmail"],  # restricted → refused
    )
    executor.execute(
        ToolCall(
            id="1",
            name="knowledge_sql",
            arguments=_json.dumps({"query": "SELECT COUNT(*) FROM knowledge_chunks"}),
        )
    )

    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert len(refused) == 1
    assert refused[0].data["agent_id"] == "agent-sql-007"
    assert refused[0].data["tool"] == "knowledge_sql"
