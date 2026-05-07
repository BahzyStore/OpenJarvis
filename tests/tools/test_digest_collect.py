"""Tests for the digest_collect tool."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from openjarvis.connectors._stubs import Document
from openjarvis.core.registry import ConnectorRegistry, ToolRegistry


def test_digest_collect_registered():
    from openjarvis.tools.digest_collect import DigestCollectTool

    ToolRegistry.register_value("digest_collect", DigestCollectTool)
    assert ToolRegistry.contains("digest_collect")


def test_digest_collect_executes():
    from openjarvis.tools.digest_collect import DigestCollectTool

    tool = DigestCollectTool()

    mock_docs = [
        Document(
            doc_id="test-1",
            source="gmail",
            doc_type="email",
            content="Meeting at 3pm",
            title="Team standup",
            author="alice@example.com",
            timestamp=datetime(2026, 4, 1, 10, 0),
        )
    ]

    mock_connector = MagicMock()
    mock_connector.return_value.is_connected.return_value = True
    mock_connector.return_value.sync.return_value = mock_docs

    with patch.object(ConnectorRegistry, "contains", return_value=True):
        with patch.object(ConnectorRegistry, "get", return_value=mock_connector):
            result = tool.execute(sources=["gmail"], hours_back=24)

    assert result.success is True
    assert "=== MESSAGES ===" in result.content
    assert "[gmail] From: alice@example.com" in result.content
    assert "Team standup" in result.content
    assert result.metadata["total_items"] == 1


def test_digest_collect_missing_connector():
    from openjarvis.tools.digest_collect import DigestCollectTool

    tool = DigestCollectTool()

    with patch.object(ConnectorRegistry, "contains", return_value=False):
        result = tool.execute(sources=["nonexistent"])

    assert result.success is True  # Partial success
    assert "not available" in result.content


# ---------------------------------------------------------------------------
# AG-9 enforcement: per-agent data-source allowlist
# ---------------------------------------------------------------------------


def _make_mock_connector_cls(source: str) -> MagicMock:
    mock_docs = [
        Document(
            doc_id=f"{source}-1",
            source=source,
            doc_type="email" if source == "gmail" else "file",
            content="hello",
            title="t",
            author="a@example.com",
            timestamp=datetime(2026, 4, 1, 10, 0),
        )
    ]
    mock_connector_cls = MagicMock()
    mock_connector_cls.return_value.is_connected.return_value = True
    mock_connector_cls.return_value.sync.return_value = mock_docs
    return mock_connector_cls


def test_digest_collect_filters_disallowed_sources():
    from openjarvis.tools.digest_collect import DigestCollectTool

    tool = DigestCollectTool()
    mock_cls = _make_mock_connector_cls("gmail")

    with patch.object(ConnectorRegistry, "contains", return_value=True):
        with patch.object(ConnectorRegistry, "get", return_value=mock_cls):
            result = tool.execute(
                sources=["gmail", "gdrive"],
                hours_back=24,
                allowed_data_sources=["gmail"],
            )

    assert result.success is True
    assert "Source 'gdrive' not permitted by agent allowlist" in result.content
    assert mock_cls.call_count == 1
    assert "gdrive" not in result.metadata["sources_ok"]
    assert "gmail" in result.metadata["sources_ok"]


def test_digest_collect_wildcard_passes_all_sources():
    from openjarvis.tools.digest_collect import DigestCollectTool

    tool = DigestCollectTool()
    mock_cls = _make_mock_connector_cls("gmail")

    with patch.object(ConnectorRegistry, "contains", return_value=True):
        with patch.object(ConnectorRegistry, "get", return_value=mock_cls):
            result = tool.execute(
                sources=["gmail", "gdrive"],
                hours_back=24,
                allowed_data_sources=["*"],
            )

    assert result.success is True
    assert "not permitted by agent allowlist" not in result.content
    assert mock_cls.call_count == 2


def test_digest_collect_no_allowlist_kwarg_unchanged_behavior():
    from openjarvis.tools.digest_collect import DigestCollectTool

    tool = DigestCollectTool()
    mock_cls = _make_mock_connector_cls("gmail")

    with patch.object(ConnectorRegistry, "contains", return_value=True):
        with patch.object(ConnectorRegistry, "get", return_value=mock_cls):
            result = tool.execute(sources=["gmail", "gdrive"], hours_back=24)

    assert result.success is True
    assert "not permitted by agent allowlist" not in result.content
    assert mock_cls.call_count == 2


def test_digest_collect_empty_allowlist_blocks_all():
    from openjarvis.tools.digest_collect import DigestCollectTool

    tool = DigestCollectTool()
    mock_cls = _make_mock_connector_cls("gmail")

    with patch.object(ConnectorRegistry, "contains", return_value=True):
        with patch.object(ConnectorRegistry, "get", return_value=mock_cls):
            result = tool.execute(
                sources=["gmail", "gdrive"],
                hours_back=24,
                allowed_data_sources=[],
            )

    assert result.success is True
    assert "Source 'gmail' not permitted by agent allowlist" in result.content
    assert "Source 'gdrive' not permitted by agent allowlist" in result.content
    assert mock_cls.call_count == 0
    assert result.metadata["sources_ok"] == []
