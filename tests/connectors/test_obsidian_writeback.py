"""Tests for ObsidianConnector write-back tools (create / append)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.connectors.obsidian import ObsidianConnector

# ---------------------------------------------------------------------------
# Fixture: an empty vault directory
# ---------------------------------------------------------------------------


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    return vault_dir


@pytest.fixture()
def connector(vault: Path) -> ObsidianConnector:
    return ObsidianConnector(vault_path=str(vault))


# ---------------------------------------------------------------------------
# create_note — happy path
# ---------------------------------------------------------------------------


def test_create_note_happy_path(connector: ObsidianConnector, vault: Path) -> None:
    """A bare title + content writes a sanitized .md file under the vault."""
    result = connector.obsidian_create_note("My Great Idea", "First line.\n")
    path = Path(result["path"])
    assert path.is_file()
    assert path.parent == vault.resolve()
    assert path.name == "my-great-idea.md"
    assert "First line." in path.read_text(encoding="utf-8")
    assert result["url"].startswith("obsidian://open?vault=")


# ---------------------------------------------------------------------------
# create_note — with folder creates a subfolder
# ---------------------------------------------------------------------------


def test_create_note_with_folder(connector: ObsidianConnector, vault: Path) -> None:
    """folder=... places the note in a subdirectory, creating it if missing."""
    result = connector.obsidian_create_note("Plan", "body", folder="Projects/Q1")
    path = Path(result["path"])
    assert path.is_file()
    assert path.relative_to(vault.resolve()) == Path("Projects/Q1/plan.md")


# ---------------------------------------------------------------------------
# create_note — with tags emits frontmatter
# ---------------------------------------------------------------------------


def test_create_note_with_tags_writes_frontmatter(
    connector: ObsidianConnector, vault: Path
) -> None:
    """tags=[...] writes a YAML frontmatter block at the top of the file."""
    result = connector.obsidian_create_note(
        "Tagged", "body content", tags=["alpha", "beta"]
    )
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "tags: [alpha, beta]" in text
    assert "body content" in text


# ---------------------------------------------------------------------------
# create_note — duplicate title auto-suffixes
# ---------------------------------------------------------------------------


def test_create_note_duplicate_auto_suffix(
    connector: ObsidianConnector, vault: Path
) -> None:
    """Creating two notes with the same title produces foo.md and foo-1.md."""
    a = connector.obsidian_create_note("Same Title", "first")
    b = connector.obsidian_create_note("Same Title", "second")
    assert Path(a["path"]).name == "same-title.md"
    assert Path(b["path"]).name == "same-title-1.md"
    assert Path(a["path"]) != Path(b["path"])


# ---------------------------------------------------------------------------
# create_note — rejects folder paths that escape the vault
# ---------------------------------------------------------------------------


def test_create_note_rejects_escape_folder(connector: ObsidianConnector) -> None:
    """folder='../escape' must raise ValueError."""
    with pytest.raises(ValueError):
        connector.obsidian_create_note("note", "body", folder="../escape")


def test_create_note_rejects_absolute_folder_outside_vault(
    connector: ObsidianConnector, tmp_path: Path
) -> None:
    """An absolute path that lives outside the vault is rejected."""
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError):
        connector.obsidian_create_note("note", "body", folder=str(outside))


# ---------------------------------------------------------------------------
# create_note — rejects empty / invalid titles
# ---------------------------------------------------------------------------


def test_create_note_rejects_empty_title(connector: ObsidianConnector) -> None:
    with pytest.raises(ValueError):
        connector.obsidian_create_note("", "body")


def test_create_note_rejects_dotdot_title(connector: ObsidianConnector) -> None:
    with pytest.raises(ValueError):
        connector.obsidian_create_note("..", "body")


def test_create_note_rejects_whitespace_only_title(
    connector: ObsidianConnector,
) -> None:
    with pytest.raises(ValueError):
        connector.obsidian_create_note("   ", "body")


# ---------------------------------------------------------------------------
# append_to_note — happy path
# ---------------------------------------------------------------------------


def test_append_to_note_happy_path(connector: ObsidianConnector, vault: Path) -> None:
    """Append writes content after two newlines."""
    target = vault / "log.md"
    target.write_text("First line.", encoding="utf-8")

    result = connector.obsidian_append_to_note("log.md", "Second line.")
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert text == "First line.\n\nSecond line."


# ---------------------------------------------------------------------------
# append_to_note — rejects path-escape attempts
# ---------------------------------------------------------------------------


def test_append_to_note_rejects_escape(connector: ObsidianConnector) -> None:
    """relative_path='../escape.md' must raise ValueError."""
    with pytest.raises(ValueError):
        connector.obsidian_append_to_note("../escape.md", "x")


# ---------------------------------------------------------------------------
# append_to_note — rejects missing file
# ---------------------------------------------------------------------------


def test_append_to_note_rejects_missing_file(connector: ObsidianConnector) -> None:
    """An untouched relative path that doesn't exist raises ValueError."""
    with pytest.raises(ValueError):
        connector.obsidian_append_to_note("does-not-exist.md", "x")


# ---------------------------------------------------------------------------
# Connector must be connected before write-back
# ---------------------------------------------------------------------------


def test_create_note_requires_connection(tmp_path: Path) -> None:
    """A connector with no vault_path raises ValueError on create."""
    conn = ObsidianConnector(vault_path="")
    with pytest.raises(ValueError):
        conn.obsidian_create_note("title", "body")


def test_append_to_note_requires_connection(tmp_path: Path) -> None:
    """A connector with no vault_path raises ValueError on append."""
    conn = ObsidianConnector(vault_path="")
    with pytest.raises(ValueError):
        conn.obsidian_append_to_note("any.md", "x")


# ---------------------------------------------------------------------------
# mcp_tools exposes the three tools
# ---------------------------------------------------------------------------


def test_mcp_tools_exposes_write_back_tools(connector: ObsidianConnector) -> None:
    """mcp_tools() includes search, create, and append."""
    names = {t.name for t in connector.mcp_tools()}
    assert names == {
        "obsidian_search_notes",
        "obsidian_create_note",
        "obsidian_append_to_note",
    }
