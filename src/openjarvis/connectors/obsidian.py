"""Obsidian / Markdown vault connector.

Reads ``.md``, ``.markdown``, and ``.txt`` files from a local vault directory,
parses optional YAML frontmatter, and yields :class:`Document` objects that
can be ingested by the knowledge pipeline.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import quote

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.registry import ConnectorRegistry
from openjarvis.tools._stubs import ToolSpec

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}

_SKIP_DIRS = {
    ".obsidian",
    ".git",
    ".trash",
    "__pycache__",
    "node_modules",
    ".venv",
}

# ---------------------------------------------------------------------------
# Frontmatter parser (no PyYAML dependency)
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Extract YAML frontmatter and return ``(metadata, body)``.

    Frontmatter is the block between two ``---`` lines at the very start of
    the file.  Only simple ``key: value`` and ``key: [a, b, c]`` syntax is
    handled — no nested YAML.
    """
    if not text.startswith("---"):
        return {}, text

    # Find the closing --- marker
    rest = text[3:]
    # Accept both \n--- and \r\n---
    end_marker = "\n---"
    end_idx = rest.find(end_marker)
    if end_idx == -1:
        return {}, text

    raw_fm = rest[:end_idx]
    # Body starts after the closing ---
    body_start = end_idx + len(end_marker)
    # Consume an optional trailing newline after the closing ---
    if body_start < len(rest) and rest[body_start] == "\n":
        body_start += 1
    body = rest[body_start:]

    metadata: Dict[str, Any] = {}
    for line in raw_fm.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            continue

        # List syntax:  [a, b, c]
        if raw_value.startswith("[") and raw_value.endswith("]"):
            inner = raw_value[1:-1]
            items = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
            metadata[key] = items
        else:
            # Strip optional surrounding quotes
            metadata[key] = raw_value.strip("'\"")

    return metadata, body


# ---------------------------------------------------------------------------
# Filename / path helpers for write-back tools
# ---------------------------------------------------------------------------


#: Characters that are problematic on Windows, macOS, or Linux filesystems,
#: plus whitespace — all collapsed to ``-`` during title sanitization.
_UNSAFE_TITLE_CHARS = re.compile(r'[\\/<>:"|?*\s]+')


def _sanitize_title(title: str) -> str:
    """Turn an arbitrary title into a safe ``.md`` filename stem.

    Rules:
    - Strip surrounding whitespace.
    - Replace ``/\\<>:"|?*`` and any whitespace with ``-``.
    - Collapse runs of ``-`` into a single dash.
    - Strip leading / trailing dashes and dots.
    - Reject the special names ``""`` and ``".."`` (raises ``ValueError``).
    """
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    cleaned = _UNSAFE_TITLE_CHARS.sub("-", title.strip()).lower()
    cleaned = re.sub(r"-+", "-", cleaned).strip("-.")
    if not cleaned or cleaned == "..":
        raise ValueError("title is empty or invalid after sanitization")
    return cleaned


def _resolve_inside_vault(vault: Path, relative: str) -> Path:
    """Resolve *relative* against *vault* and verify it stays inside.

    Raises :class:`ValueError` if the resolved path escapes the vault.
    """
    vault_resolved = vault.resolve()
    candidate = (vault_resolved / relative).resolve()
    try:
        candidate.relative_to(vault_resolved)
    except ValueError as exc:
        raise ValueError(
            f"path '{relative}' escapes vault root {vault_resolved}"
        ) from exc
    return candidate


def _format_frontmatter(tags: Iterable[str]) -> str:
    """Render a YAML frontmatter block with the given tags list."""
    tag_list = [str(t).strip() for t in tags if str(t).strip()]
    if not tag_list:
        return ""
    rendered = ", ".join(tag_list)
    return f"---\ntags: [{rendered}]\n---\n"


# ---------------------------------------------------------------------------
# ObsidianConnector
# ---------------------------------------------------------------------------


@ConnectorRegistry.register("obsidian")
class ObsidianConnector(BaseConnector):
    """Connector that reads a local Obsidian (or plain Markdown) vault.

    Parameters
    ----------
    vault_path:
        Absolute path to the vault root directory.  An empty string means
        "not yet configured" — :meth:`is_connected` will return ``False``.
    """

    connector_id = "obsidian"
    display_name = "Obsidian / Markdown"
    auth_type = "filesystem"

    def __init__(self, vault_path: str = "") -> None:
        self._vault_path = vault_path
        self._connected: bool = bool(vault_path) and Path(vault_path).is_dir()
        self._items_synced: int = 0
        self._items_total: int = 0

    # ------------------------------------------------------------------
    # BaseConnector interface
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        """Return ``True`` if vault_path is set and the directory exists."""
        return bool(self._vault_path) and Path(self._vault_path).is_dir()

    def disconnect(self) -> None:
        """Clear vault_path and mark as disconnected."""
        self._vault_path = ""
        self._connected = False

    def sync(
        self,
        *,
        since: Optional[datetime] = None,
        cursor: Optional[str] = None,  # noqa: ARG002 — unused but part of ABC
    ) -> Iterator[Document]:
        """Walk the vault and yield one :class:`Document` per text file.

        Parameters
        ----------
        since:
            If provided, skip files whose mtime is before this datetime.
        cursor:
            Not used for this filesystem connector (included for API
            compatibility).
        """
        vault = Path(self._vault_path)
        vault_name = vault.name

        collected_paths: List[Path] = []
        for root, dirs, files in os.walk(vault):
            # Prune hidden and known-junk directories in-place so os.walk
            # does not descend into them.
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]

            for filename in files:
                fpath = Path(root) / filename
                if fpath.suffix.lower() not in _TEXT_EXTENSIONS:
                    continue
                collected_paths.append(fpath)

        self._items_total = len(collected_paths)
        synced = 0

        for fpath in collected_paths:
            # Apply since filter based on mtime
            mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
            if since is not None and mtime < since:
                continue

            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                continue

            metadata, _body = _parse_frontmatter(text)

            title = metadata.get("title") or fpath.stem
            rel_path = fpath.relative_to(vault)

            url = (
                f"obsidian://open?vault={quote(vault_name)}&file={quote(str(rel_path))}"
            )

            doc = Document(
                doc_id=f"obsidian:{rel_path}",
                source="obsidian",
                doc_type="note",
                content=text,
                title=str(title),
                timestamp=mtime,
                url=url,
                metadata={k: v for k, v in metadata.items() if k != "title"},
            )
            synced += 1
            yield doc

        self._items_synced = synced

    def sync_status(self) -> SyncStatus:
        """Return sync progress from the most recent :meth:`sync` call."""
        return SyncStatus(
            state="idle",
            items_synced=self._items_synced,
            items_total=self._items_total,
        )

    # ------------------------------------------------------------------
    # Write-back operations
    # ------------------------------------------------------------------

    def obsidian_create_note(
        self,
        title: str,
        content: str,
        folder: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Create a new ``.md`` note inside the vault.

        Parameters
        ----------
        title:
            Human-readable title; sanitized to form the filename stem.
        content:
            Markdown body for the note (frontmatter is added separately).
        folder:
            Optional subfolder (relative to vault root).  Created if it
            doesn't exist.  Must not escape the vault.
        tags:
            Optional list of tags to render as YAML frontmatter.

        Returns
        -------
        dict
            ``{"path": <absolute path>, "url": <obsidian:// deep link>}``.

        Raises
        ------
        ValueError
            For invalid titles, folders escaping the vault, or when the
            connector is not configured.
        """
        if not self.is_connected():
            raise ValueError("Obsidian connector is not connected")

        vault = Path(self._vault_path)
        stem = _sanitize_title(title)

        # Resolve the target directory (vault root or a subfolder).
        if folder:
            target_dir = _resolve_inside_vault(vault, folder)
        else:
            target_dir = vault.resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        # Auto-suffix on collision: foo.md, foo-1.md, foo-2.md, ...
        candidate = target_dir / f"{stem}.md"
        suffix_n = 1
        while candidate.exists():
            candidate = target_dir / f"{stem}-{suffix_n}.md"
            suffix_n += 1

        # Re-check that the final path is inside the vault (defence in depth
        # against a folder argument that somehow widened the resolve set).
        _resolve_inside_vault(vault, str(candidate.relative_to(vault.resolve())))

        body_parts: List[str] = []
        if tags:
            fm = _format_frontmatter(tags)
            if fm:
                body_parts.append(fm)
        body_parts.append(content)
        candidate.write_text("".join(body_parts), encoding="utf-8")

        rel_path = candidate.relative_to(vault.resolve())
        url = f"obsidian://open?vault={quote(vault.name)}&file={quote(str(rel_path))}"
        return {"path": str(candidate), "url": url}

    def obsidian_append_to_note(
        self, relative_path: str, content: str
    ) -> Dict[str, str]:
        """Append *content* to an existing note inside the vault.

        Parameters
        ----------
        relative_path:
            Path of the target note relative to the vault root.  Must stay
            inside the vault.
        content:
            Text to append.  Two newlines are inserted before it for
            visual separation from the existing body.

        Returns
        -------
        dict
            ``{"path": <absolute path>, "url": <obsidian:// deep link>}``.

        Raises
        ------
        ValueError
            If the connector is not connected, the path escapes the
            vault, or the file does not exist.
        """
        if not self.is_connected():
            raise ValueError("Obsidian connector is not connected")

        vault = Path(self._vault_path)
        target = _resolve_inside_vault(vault, relative_path)
        if not target.is_file():
            raise ValueError(f"note not found: {relative_path}")

        with target.open("a", encoding="utf-8") as fh:
            fh.write(f"\n\n{content}")

        rel_path = target.relative_to(vault.resolve())
        url = f"obsidian://open?vault={quote(vault.name)}&file={quote(str(rel_path))}"
        return {"path": str(target), "url": url}

    # ------------------------------------------------------------------
    # MCP tools
    # ------------------------------------------------------------------

    def mcp_tools(self) -> List[ToolSpec]:
        """Expose search + write-back tools for agent use."""
        return [
            ToolSpec(
                name="obsidian_search_notes",
                description=(
                    "Search notes in the local Obsidian vault by keyword. "
                    "Returns matching note titles and snippets."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query string",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Maximum number of results to return",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
                category="knowledge",
            ),
            ToolSpec(
                name="obsidian_create_note",
                description=(
                    "Create a new Markdown note in the Obsidian vault. "
                    "The title becomes the filename (sanitized). Returns "
                    "the absolute path and an obsidian:// deep link."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Human-readable note title.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Markdown body of the note.",
                        },
                        "folder": {
                            "type": "string",
                            "description": (
                                "Optional subfolder inside the vault. "
                                "Created if missing."
                            ),
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional list of tags to write as YAML frontmatter."
                            ),
                        },
                    },
                    "required": ["title", "content"],
                },
                category="knowledge",
                requires_confirmation=False,
            ),
            ToolSpec(
                name="obsidian_append_to_note",
                description=(
                    "Append text to an existing note in the Obsidian "
                    "vault. The relative_path must stay inside the vault."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "relative_path": {
                            "type": "string",
                            "description": (
                                "Path of the target note relative to "
                                "the vault root (e.g. "
                                "'Projects/Inbox.md')."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": "Text to append.",
                        },
                    },
                    "required": ["relative_path", "content"],
                },
                category="knowledge",
                requires_confirmation=False,
            ),
        ]
