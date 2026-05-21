"""JSON-based persistence for connector instance configs.

Stores per-connector configuration (e.g. ``vault_path`` for Obsidian,
filesystem paths for other local connectors) in
``~/.config/openjarvis/connectors.json`` so that connections survive a
backend restart without requiring the user to re-issue ``/connect`` calls.

This is intentionally a small, dependency-free module: no pydantic, no
sqlite — just a single JSON file.  Sensitive values (OAuth tokens) are
already handled separately by ``openjarvis.connectors.oauth`` and the
``secrets_store``; this module is for non-secret configuration only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

#: Directory holding connector configs.  Created on first write.
CONFIG_DIR: Path = Path.home() / ".config" / "openjarvis"

#: JSON file mapping ``connector_id -> config dict``.
CONFIG_FILE: Path = CONFIG_DIR / "connectors.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_all() -> Dict[str, Dict[str, Any]]:
    """Read and return the entire connector-config map.

    Returns an empty dict when the file is missing or contains invalid
    JSON — persistence is best-effort and must never raise on a corrupted
    file.
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        text = CONFIG_FILE.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s: %s", CONFIG_FILE, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    # Filter to only dict-valued entries — protects callers from a hand-
    # edited file with bogus shapes.
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _write_all(data: Dict[str, Dict[str, Any]]) -> None:
    """Atomically write the connector-config map to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def _sanitize(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *config* containing only JSON-serializable values.

    Non-serializable values are stringified rather than dropped so the
    caller sees evidence of the problem on read-back.  Keys that aren't
    strings are skipped entirely.
    """
    safe: Dict[str, Any] = {}
    for key, value in config.items():
        if not isinstance(key, str):
            continue
        try:
            json.dumps(value)
            safe[key] = value
        except (TypeError, ValueError):
            safe[key] = str(value)
    return safe


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save_connector_config(connector_id: str, config: Dict[str, Any]) -> None:
    """Persist *config* for *connector_id*.

    Overwrites any existing entry for the same connector.  Non-serializable
    values are stringified before storage.
    """
    if not connector_id:
        raise ValueError("connector_id must be non-empty")
    data = _read_all()
    data[connector_id] = _sanitize(config)
    _write_all(data)


def load_connector_config(connector_id: str) -> Optional[Dict[str, Any]]:
    """Return the saved config for *connector_id*, or ``None`` if absent."""
    if not connector_id:
        return None
    data = _read_all()
    return data.get(connector_id)


def load_all_connector_configs() -> Dict[str, Dict[str, Any]]:
    """Return a dict of ``{connector_id: config}`` for every saved entry."""
    return _read_all()


def clear_connector_config(connector_id: str) -> None:
    """Remove the saved config for *connector_id* (no-op if absent)."""
    data = _read_all()
    if connector_id in data:
        del data[connector_id]
        _write_all(data)


# ---------------------------------------------------------------------------
# Default-vault seeding (Deliverable 3)
# ---------------------------------------------------------------------------


_WELCOME_BODY = (
    "# Welcome to OpenJarvis\n\n"
    "This is your default AtomicX vault.  Notes you create from Jarvis "
    "land here, and Jarvis can read everything in this folder.\n\n"
    "Edit, move, or delete anything you like — it's just Markdown.\n"
)

_ATOMICX_OVERVIEW = (
    "---\n"
    "tags: [atomicx, starter]\n"
    "---\n"
    "# AtomicX Overview\n\n"
    "AtomicX is your top-level workspace.  Use it for long-running themes "
    "(clients, products, ongoing research) that span multiple notes.\n"
)

_ATOMICX_CLIENTS = (
    "---\n"
    "tags: [atomicx, starter]\n"
    "---\n"
    "# Clients\n\n"
    "One note per client.  Link from here as you add them.\n"
)

_PROJECTS_INBOX = (
    "# Inbox\n\nCapture quick thoughts here, then move them into a project folder.\n"
)


def _default_vault_path() -> Path:
    """Return the preferred default vault location.

    Prefers ``/mnt/c/Users/ahmed/AtomicX-Vault`` (WSL2 -> Windows host)
    when that Windows-side directory exists; falls back to
    ``~/AtomicX-Vault`` otherwise.
    """
    windows_root = Path("/mnt/c/Users/ahmed")
    if windows_root.is_dir():
        return windows_root / "AtomicX-Vault"
    return Path.home() / "AtomicX-Vault"


def ensure_default_vault() -> Optional[Path]:
    """Create and seed a default AtomicX vault when no obsidian config exists.

    Returns the vault :class:`~pathlib.Path` on success, or ``None`` if
    setup failed.  Idempotent: if the obsidian config is already saved
    or the vault is already populated, this function is a no-op.
    """
    if load_connector_config("obsidian") is not None:
        return None

    vault = _default_vault_path()
    try:
        vault.mkdir(parents=True, exist_ok=True)
        (vault / "AtomicX").mkdir(exist_ok=True)
        (vault / "Projects").mkdir(exist_ok=True)

        # Seed starter notes — but never overwrite existing files.
        seed_files = [
            (vault / "Welcome.md", _WELCOME_BODY),
            (vault / "AtomicX" / "Overview.md", _ATOMICX_OVERVIEW),
            (vault / "AtomicX" / "Clients.md", _ATOMICX_CLIENTS),
            (vault / "Projects" / "Inbox.md", _PROJECTS_INBOX),
        ]
        for path, body in seed_files:
            if not path.exists():
                path.write_text(body, encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not seed default vault at %s: %s", vault, exc)
        return None

    save_connector_config("obsidian", {"vault_path": str(vault)})
    logger.info("Seeded default AtomicX vault at %s", vault)
    return vault


__all__ = [
    "CONFIG_DIR",
    "CONFIG_FILE",
    "clear_connector_config",
    "ensure_default_vault",
    "load_all_connector_configs",
    "load_connector_config",
    "save_connector_config",
]
