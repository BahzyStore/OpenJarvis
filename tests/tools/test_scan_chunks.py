"""Tests for ScanChunksTool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openjarvis.connectors.store import KnowledgeStore
from openjarvis.core.registry import ToolRegistry


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeStore:
    ks = KnowledgeStore(str(tmp_path / "test.db"))
    ks.store("Met with Sequoia about Series A", source="granola", doc_type="document")
    ks.store("Fundraising discussion with a16z", source="granola", doc_type="document")
    ks.store("Weekly standup notes", source="granola", doc_type="document")
    ks.store("Trip to Spain with family", source="imessage", doc_type="message")
    return ks


def _fake_engine() -> MagicMock:
    engine = MagicMock()
    engine.generate.return_value = {
        "content": "Found: Sequoia Series A discussion, a16z fundraising",
        "usage": {},
    }
    return engine


def test_scan_finds_semantic_matches(store: KnowledgeStore) -> None:
    from openjarvis.tools.scan_chunks import ScanChunksTool

    engine = _fake_engine()
    tool = ScanChunksTool(store=store, engine=engine, model="test")
    result = tool.execute(question="Which VCs have I spoken with?")
    assert result.success
    assert "Sequoia" in result.content or "Found" in result.content
    assert engine.generate.called


def test_scan_respects_source_filter(store: KnowledgeStore) -> None:
    from openjarvis.tools.scan_chunks import ScanChunksTool

    engine = _fake_engine()
    tool = ScanChunksTool(store=store, engine=engine, model="test")
    result = tool.execute(question="What trips?", source="imessage")
    assert result.success
    call_args = engine.generate.call_args
    messages = call_args[0][0] if call_args[0] else call_args[1].get("messages", [])
    all_content = str(messages)
    assert "Spain" in all_content


def test_scan_empty_store(tmp_path: Path) -> None:
    from openjarvis.tools.scan_chunks import ScanChunksTool

    ks = KnowledgeStore(str(tmp_path / "empty.db"))
    engine = _fake_engine()
    tool = ScanChunksTool(store=ks, engine=engine, model="test")
    result = tool.execute(question="Anything?")
    assert result.success
    assert "no chunks" in result.content.lower() or result.content == ""


def test_registered() -> None:
    from openjarvis.tools.scan_chunks import ScanChunksTool

    ToolRegistry.register_value("scan_chunks", ScanChunksTool)
    assert ToolRegistry.contains("scan_chunks")


# ---------------------------------------------------------------------------
# AG-9: per-agent data-source allowlist enforcement
# ---------------------------------------------------------------------------


def test_explicit_source_denied_refuses_and_engine_not_called(
    store: KnowledgeStore,
) -> None:
    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.tools.scan_chunks import ScanChunksTool

    # Reset to discard MEMORY_STORE events emitted by the `store` fixture's
    # setup, which created the singleton with record_history=False.
    reset_event_bus()
    bus = get_event_bus(record_history=True)

    engine = _fake_engine()
    tool = ScanChunksTool(store=store, engine=engine, model="test")
    result = tool.execute(
        question="What trips?",
        source="imessage",
        allowed_data_sources=["granola"],
    )

    assert result.success is False
    assert "not permitted by agent allowlist" in result.content
    assert result.metadata["refused_source"] == "imessage"
    # Critical: the LLM must NOT have been called for a denied source.
    assert engine.generate.call_count == 0

    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert len(refused) == 1
    assert refused[0].data["source_id"] == "imessage"
    assert refused[0].data["tool"] == "scan_chunks"
    assert refused[0].data["allowed_data_sources"] == ["granola"]


def test_explicit_source_allowed_passes(store: KnowledgeStore) -> None:
    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.tools.scan_chunks import ScanChunksTool

    reset_event_bus()
    bus = get_event_bus(record_history=True)

    engine = _fake_engine()
    tool = ScanChunksTool(store=store, engine=engine, model="test")
    result = tool.execute(
        question="What trips?",
        source="imessage",
        allowed_data_sources=["imessage"],
    )

    assert result.success is True
    assert engine.generate.called
    # The fed prompt should contain imessage content (Spain trip).
    call_args = engine.generate.call_args
    messages = call_args[0][0]
    assert "Spain" in str(messages)

    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert refused == []


def test_no_source_post_filters_before_llm(store: KnowledgeStore) -> None:
    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.tools.scan_chunks import ScanChunksTool

    reset_event_bus()
    bus = get_event_bus(record_history=True)

    engine = _fake_engine()
    tool = ScanChunksTool(store=store, engine=engine, model="test")
    # source="" (unfiltered SQL would return both granola + imessage rows).
    # With allowed=["granola"], imessage rows must be dropped BEFORE the LLM
    # sees them — verifying the core security property.
    result = tool.execute(
        question="Anything?",
        allowed_data_sources=["granola"],
    )

    assert result.success is True
    assert engine.generate.called
    # The LLM prompt must NOT contain imessage content.
    all_call_text = ""
    for call in engine.generate.call_args_list:
        all_call_text += str(call)
    assert "Spain" not in all_call_text
    # The granola rows should have made it through.
    assert (
        "Sequoia" in all_call_text
        or "a16z" in all_call_text
        or "standup" in all_call_text
    )

    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert len(refused) == 1
    assert refused[0].data["source_id"] is None
    assert refused[0].data["dropped_sources"] == ["imessage"]
    assert refused[0].data["tool"] == "scan_chunks"


def test_wildcard_allows_everything(store: KnowledgeStore) -> None:
    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.tools.scan_chunks import ScanChunksTool

    reset_event_bus()
    bus = get_event_bus(record_history=True)

    engine = _fake_engine()
    tool = ScanChunksTool(store=store, engine=engine, model="test")
    result = tool.execute(
        question="Anything?",
        allowed_data_sources=["*"],
    )

    assert result.success is True
    assert engine.generate.called
    # Wildcard preserves unfiltered behavior — all sources reach the LLM.
    all_call_text = ""
    for call in engine.generate.call_args_list:
        all_call_text += str(call)
    assert "Spain" in all_call_text  # imessage row included

    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert refused == []


def test_empty_allowlist_drops_all(store: KnowledgeStore) -> None:
    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.tools.scan_chunks import ScanChunksTool

    reset_event_bus()
    bus = get_event_bus(record_history=True)

    engine = _fake_engine()
    tool = ScanChunksTool(store=store, engine=engine, model="test")
    result = tool.execute(
        question="Anything?",
        allowed_data_sources=[],
    )

    assert result.success is True
    assert result.metadata["chunks_scanned"] == 0
    # No rows survived post-filter — engine should not be called.
    assert engine.generate.call_count == 0

    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert len(refused) == 1
    assert set(refused[0].data["dropped_sources"]) >= {"granola", "imessage"}


def test_no_kwarg_unchanged_behavior(store: KnowledgeStore) -> None:
    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.tools.scan_chunks import ScanChunksTool

    reset_event_bus()
    bus = get_event_bus(record_history=True)

    engine = _fake_engine()
    tool = ScanChunksTool(store=store, engine=engine, model="test")
    # No allowed_data_sources kwarg → legacy path, all rows reach the LLM.
    result = tool.execute(question="Anything?")

    assert result.success is True
    assert engine.generate.called

    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert refused == []


def test_refusal_includes_agent_id_via_dispatcher(store: KnowledgeStore) -> None:
    """End-to-end: ToolExecutor injects _agent_id, both the explicit
    refusal and post-filter summary emit paths forward it to the
    DATA_SOURCE_REFUSED payload."""
    import json as _json

    from openjarvis.core.events import EventType, get_event_bus, reset_event_bus
    from openjarvis.core.types import ToolCall
    from openjarvis.tools._stubs import ToolExecutor
    from openjarvis.tools.scan_chunks import ScanChunksTool

    reset_event_bus()
    bus = get_event_bus(record_history=True)

    engine = _fake_engine()
    tool = ScanChunksTool(store=store, engine=engine, model="test")
    executor = ToolExecutor(
        [tool],
        agent_id="agent-scan-007",
        allowed_data_sources=["granola"],  # imessage will be refused
    )
    executor.execute(
        ToolCall(
            id="1",
            name="scan_chunks",
            arguments=_json.dumps({"question": "What trips?", "source": "imessage"}),
        )
    )

    refused = [e for e in bus.history if e.event_type == EventType.DATA_SOURCE_REFUSED]
    assert len(refused) == 1
    assert refused[0].data["agent_id"] == "agent-scan-007"
    assert refused[0].data["source_id"] == "imessage"
