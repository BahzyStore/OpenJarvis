"""Tests for the knowledge_search tool."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from openjarvis.connectors.store import KnowledgeStore
from openjarvis.core.registry import ToolRegistry
from openjarvis.tools.knowledge_search import KnowledgeSearchTool

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    """Return a KnowledgeStore pre-loaded with 3 diverse items."""
    s = KnowledgeStore(db_path=tmp_path / "test_knowledge.db")

    # Item 1 — gmail email from alice
    s.store(
        "Meeting about Kubernetes migration scheduled for next Tuesday.",
        source="gmail",
        doc_type="email",
        title="Re: K8s migration",
        author="alice@example.com",
        url="https://mail.google.com/mail/u/0/#inbox/abc123",
        timestamp="2026-01-15T10:00:00Z",
    )

    # Item 2 — slack message from bob
    s.store(
        "Discussion about K8s costs — we should consider spot instances.",
        source="slack",
        doc_type="message",
        title="#infrastructure",
        author="bob@example.com",
        url="slack://thread/def456",
        timestamp="2026-01-20T14:30:00Z",
    )

    # Item 3 — obsidian document from sarah
    s.store(
        "Research notes on large language model fine-tuning strategies.",
        source="obsidian",
        doc_type="document",
        title="LLM Fine-tuning Notes",
        author="sarah@example.com",
        url="obsidian://vault/llm-notes",
        timestamp="2026-02-01T09:00:00Z",
    )

    yield s
    s.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKnowledgeSearchTool:
    def test_basic_search(self, store):
        """Search finds the matching document from the 3-item store."""
        tool = KnowledgeSearchTool(store=store)
        result = tool.execute(query="Kubernetes migration")
        assert result.success is True
        assert "Kubernetes" in result.content
        assert result.metadata["num_results"] >= 1

    def test_filter_by_source(self, store):
        """source filter restricts results to gmail only."""
        tool = KnowledgeSearchTool(store=store)
        result = tool.execute(query="Kubernetes", source="gmail")
        assert result.success is True
        assert "gmail" in result.content
        # Every returned result must come from gmail
        assert "slack" not in result.content

    def test_filter_by_author(self, store):
        """author filter restricts results to sarah only."""
        tool = KnowledgeSearchTool(store=store)
        # Use "language model" — FTS5 does not tokenise hyphenated terms like
        # "fine-tuning" as a single token, so we search for a phrase that works.
        result = tool.execute(query="language model", author="sarah@example.com")
        assert result.success is True
        assert "sarah@example.com" in result.content
        assert result.metadata["num_results"] >= 1

    def test_no_results(self, store):
        """Query matching nothing returns success=True with 'No relevant results'."""
        tool = KnowledgeSearchTool(store=store)
        result = tool.execute(query="zzz_nonexistent_xyzzy_12345")
        assert result.success is True
        assert "No relevant results" in result.content
        assert result.metadata["num_results"] == 0

    def test_empty_query(self, store):
        """Empty query string returns success=False."""
        tool = KnowledgeSearchTool(store=store)
        result = tool.execute(query="")
        assert result.success is False
        assert "No query provided" in result.content

    def test_no_store(self):
        """Missing store returns success=False."""
        tool = KnowledgeSearchTool()
        result = tool.execute(query="kubernetes")
        assert result.success is False
        assert "No knowledge store configured" in result.content

    def test_spec_has_filter_params(self):
        """ToolSpec.parameters includes all required and optional filter fields."""
        tool = KnowledgeSearchTool()
        props = tool.spec.parameters.get("properties", {})
        for field in ("query", "source", "doc_type", "author", "since", "top_k"):
            assert field in props, f"Missing parameter: {field}"
        assert "query" in tool.spec.parameters.get("required", [])
        assert tool.spec.category == "knowledge"

    def test_registry(self):
        """ToolRegistry contains 'knowledge_search' after module import.

        The autouse ``_clean_registries`` fixture clears all registries before
        each test.  Since the module is already cached in ``sys.modules`` a
        plain import won't re-execute the ``@ToolRegistry.register`` decorator,
        so we explicitly reload the module.
        """
        mod_name = "openjarvis.tools.knowledge_search"
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            importlib.import_module(mod_name)

        assert ToolRegistry.contains("knowledge_search")


def test_tool_uses_two_stage_retriever(tmp_path: Path) -> None:
    """KnowledgeSearchTool delegates to TwoStageRetriever when supplied."""
    from openjarvis.connectors.retriever import TwoStageRetriever

    store = KnowledgeStore(db_path=str(tmp_path / "ts_test.db"))
    store.store(
        content="Deep learning research paper", source="gdrive", doc_type="document"
    )
    retriever = TwoStageRetriever(store=store)
    tool = KnowledgeSearchTool(store=store, retriever=retriever)
    result = tool.execute(query="deep learning")
    assert result.success
    assert result.metadata["num_results"] > 0


# ---------------------------------------------------------------------------
# AG-9: per-agent data-source allowlist enforcement
# ---------------------------------------------------------------------------


class TestKnowledgeSearchAllowlist:
    """Enforce AG-9 allowlist for both explicit-source and unfiltered queries."""

    def test_explicit_source_denied_refuses(self, store):
        from openjarvis.core.events import EventType, get_event_bus, reset_event_bus

        # Reset to discard any MEMORY_STORE events emitted during the
        # `store` fixture's setup, which created the singleton with
        # record_history=False (the kwarg only applies on first creation).
        reset_event_bus()
        bus = get_event_bus(record_history=True)
        tool = KnowledgeSearchTool(store=store)
        result = tool.execute(
            query="Kubernetes",
            source="gmail",
            allowed_data_sources=["slack"],
        )
        assert result.success is False
        assert "not permitted by agent allowlist" in result.content
        assert result.metadata["refused_source"] == "gmail"

        refused = [
            e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED
        ]
        assert len(refused) == 1
        assert refused[0].data["source_id"] == "gmail"
        assert refused[0].data["tool"] == "knowledge_search"
        assert refused[0].data["allowed_data_sources"] == ["slack"]

    def test_explicit_source_allowed_passes(self, store):
        from openjarvis.core.events import EventType, get_event_bus, reset_event_bus

        # Reset to discard any MEMORY_STORE events emitted during the
        # `store` fixture's setup, which created the singleton with
        # record_history=False (the kwarg only applies on first creation).
        reset_event_bus()
        bus = get_event_bus(record_history=True)
        tool = KnowledgeSearchTool(store=store)
        result = tool.execute(
            query="Kubernetes",
            source="gmail",
            allowed_data_sources=["gmail"],
        )
        assert result.success is True
        assert "gmail" in result.content
        assert "slack" not in result.content

        refused = [
            e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED
        ]
        assert refused == []

    def test_no_source_post_filters_results(self, store):
        from openjarvis.core.events import EventType, get_event_bus, reset_event_bus

        # Reset to discard any MEMORY_STORE events emitted during the
        # `store` fixture's setup, which created the singleton with
        # record_history=False (the kwarg only applies on first creation).
        reset_event_bus()
        bus = get_event_bus(record_history=True)
        tool = KnowledgeSearchTool(store=store)
        # Query "K8s" matches both gmail (item 1) and slack (item 2).
        # Allowing only gmail should drop the slack result and emit one event.
        result = tool.execute(
            query="K8s",
            allowed_data_sources=["gmail"],
        )
        assert result.success is True
        assert "gmail" in result.content
        assert "slack" not in result.content

        refused = [
            e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED
        ]
        assert len(refused) == 1
        assert refused[0].data["source_id"] is None
        assert "slack" in refused[0].data["dropped_sources"]
        assert refused[0].data["tool"] == "knowledge_search"

    def test_no_kwarg_unchanged_behavior(self, store):
        from openjarvis.core.events import EventType, get_event_bus, reset_event_bus

        # Reset to discard any MEMORY_STORE events emitted during the
        # `store` fixture's setup, which created the singleton with
        # record_history=False (the kwarg only applies on first creation).
        reset_event_bus()
        bus = get_event_bus(record_history=True)
        tool = KnowledgeSearchTool(store=store)
        result = tool.execute(query="K8s")  # no allowlist kwarg
        assert result.success is True
        # Both gmail + slack results returned (no allowlist applied)
        assert "gmail" in result.content
        assert "slack" in result.content

        refused = [
            e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED
        ]
        assert refused == []

    def test_wildcard_allows_everything(self, store):
        from openjarvis.core.events import EventType, get_event_bus, reset_event_bus

        # Reset to discard any MEMORY_STORE events emitted during the
        # `store` fixture's setup, which created the singleton with
        # record_history=False (the kwarg only applies on first creation).
        reset_event_bus()
        bus = get_event_bus(record_history=True)
        tool = KnowledgeSearchTool(store=store)
        result = tool.execute(
            query="K8s",
            allowed_data_sources=["*"],
        )
        assert result.success is True
        # Wildcard preserves unfiltered behavior — both sources kept.
        assert "gmail" in result.content
        assert "slack" in result.content

        refused = [
            e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED
        ]
        assert refused == []

    def test_empty_allowlist_drops_all_in_post_filter(self, store):
        from openjarvis.core.events import EventType, get_event_bus, reset_event_bus

        # Reset to discard any MEMORY_STORE events emitted during the
        # `store` fixture's setup, which created the singleton with
        # record_history=False (the kwarg only applies on first creation).
        reset_event_bus()
        bus = get_event_bus(record_history=True)
        tool = KnowledgeSearchTool(store=store)
        result = tool.execute(
            query="K8s",
            allowed_data_sources=[],
        )
        # Every result dropped → "No relevant results" path.
        assert result.success is True
        assert result.metadata["num_results"] == 0

        refused = [
            e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED
        ]
        # One summary event listing all dropped sources.
        assert len(refused) == 1
        assert set(refused[0].data["dropped_sources"]) >= {"gmail", "slack"}

    def test_refusal_includes_agent_id_via_dispatcher(self, store):
        """End-to-end: ToolExecutor injects _agent_id, the tool's emit
        path forwards it to the DATA_SOURCE_REFUSED payload."""
        import json as _json

        from openjarvis.core.events import (
            EventType,
            get_event_bus,
            reset_event_bus,
        )
        from openjarvis.core.types import ToolCall
        from openjarvis.tools._stubs import ToolExecutor

        reset_event_bus()
        bus = get_event_bus(record_history=True)

        tool = KnowledgeSearchTool(store=store)
        executor = ToolExecutor(
            [tool],
            agent_id="agent-ksearch-007",
            allowed_data_sources=["slack"],  # gmail will be refused
        )
        executor.execute(
            ToolCall(
                id="1",
                name="knowledge_search",
                arguments=_json.dumps({"query": "Kubernetes", "source": "gmail"}),
            )
        )

        refused = [
            e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED
        ]
        assert len(refused) == 1
        assert refused[0].data["agent_id"] == "agent-ksearch-007"
        assert refused[0].data["source_id"] == "gmail"
