"""Tests for connector config persistence — JSON-backed instance configs."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from openjarvis.connectors import persistence

# ---------------------------------------------------------------------------
# Fixture: redirect the persistence file under tmp_path so tests don't
# touch the user's real ~/.config/openjarvis/connectors.json.
# ---------------------------------------------------------------------------


@pytest.fixture()
def redirected_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Point CONFIG_DIR / CONFIG_FILE at *tmp_path* for the duration of the test."""
    config_dir = tmp_path / ".config" / "openjarvis"
    config_file = config_dir / "connectors.json"
    monkeypatch.setattr(persistence, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(persistence, "CONFIG_FILE", config_file)
    yield config_file


# ---------------------------------------------------------------------------
# Test 1: save then load round-trips correctly
# ---------------------------------------------------------------------------


def test_save_then_load_round_trips(redirected_config: Path) -> None:
    """save_connector_config / load_connector_config preserve the dict."""
    payload = {"vault_path": "/tmp/myvault", "extra": "value"}
    persistence.save_connector_config("obsidian", payload)
    loaded = persistence.load_connector_config("obsidian")
    assert loaded == payload


# ---------------------------------------------------------------------------
# Test 2: loading a missing connector returns None
# ---------------------------------------------------------------------------


def test_load_missing_returns_none(redirected_config: Path) -> None:
    """A connector that was never saved returns None on load."""
    assert persistence.load_connector_config("never-saved") is None


def test_load_all_returns_empty_dict_when_no_file(redirected_config: Path) -> None:
    """load_all_connector_configs returns {} when the file doesn't exist."""
    assert persistence.load_all_connector_configs() == {}


# ---------------------------------------------------------------------------
# Test 3: file path uses ~/.config/openjarvis/
# ---------------------------------------------------------------------------


def test_config_file_path_is_home_dot_config() -> None:
    """CONFIG_FILE resolves under ~/.config/openjarvis/connectors.json."""
    # We re-import to grab the unpatched module-level constants.
    from openjarvis.connectors import persistence as p

    expected = Path.home() / ".config" / "openjarvis" / "connectors.json"
    assert p.CONFIG_FILE == expected
    assert p.CONFIG_DIR == expected.parent


# ---------------------------------------------------------------------------
# Test 4: save tolerates non-serializable values gracefully (stringifies)
# ---------------------------------------------------------------------------


def test_save_handles_non_serializable_value(redirected_config: Path) -> None:
    """A value that json.dumps can't handle is stringified, not raised."""

    class Opaque:
        def __str__(self) -> str:
            return "opaque-repr"

    persistence.save_connector_config(
        "weird", {"ok": 1, "bad": Opaque(), "also_ok": [1, 2]}
    )
    loaded = persistence.load_connector_config("weird")
    assert loaded is not None
    assert loaded["ok"] == 1
    assert loaded["also_ok"] == [1, 2]
    assert loaded["bad"] == "opaque-repr"


# ---------------------------------------------------------------------------
# Test 5: clear_connector_config removes the entry
# ---------------------------------------------------------------------------


def test_clear_connector_config(redirected_config: Path) -> None:
    """After clear, the entry is gone and load returns None."""
    persistence.save_connector_config("obsidian", {"vault_path": "/x"})
    assert persistence.load_connector_config("obsidian") is not None
    persistence.clear_connector_config("obsidian")
    assert persistence.load_connector_config("obsidian") is None


def test_clear_missing_is_noop(redirected_config: Path) -> None:
    """Clearing a non-existent entry doesn't raise."""
    persistence.clear_connector_config("not-there")  # should not raise


# ---------------------------------------------------------------------------
# Test 6: empty connector_id raises on save, returns None on load
# ---------------------------------------------------------------------------


def test_save_rejects_empty_connector_id(redirected_config: Path) -> None:
    with pytest.raises(ValueError):
        persistence.save_connector_config("", {"x": 1})


def test_load_empty_id_returns_none(redirected_config: Path) -> None:
    assert persistence.load_connector_config("") is None


# ---------------------------------------------------------------------------
# Test 7: corrupted JSON file is treated as empty (no crash)
# ---------------------------------------------------------------------------


def test_corrupt_file_treated_as_empty(redirected_config: Path) -> None:
    """A malformed JSON file does not propagate an exception."""
    redirected_config.parent.mkdir(parents=True, exist_ok=True)
    redirected_config.write_text("{not valid json", encoding="utf-8")
    assert persistence.load_all_connector_configs() == {}
    assert persistence.load_connector_config("anything") is None


# ---------------------------------------------------------------------------
# Test 8: ensure_default_vault seeds files and saves config
# ---------------------------------------------------------------------------


def test_ensure_default_vault_seeds_when_first_run(
    redirected_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First run creates the vault directory + starter notes and saves config."""
    vault = tmp_path / "AtomicX-Vault"
    monkeypatch.setattr(persistence, "_default_vault_path", lambda: vault)

    result = persistence.ensure_default_vault()
    assert result == vault
    assert (vault / "Welcome.md").is_file()
    assert (vault / "AtomicX" / "Overview.md").is_file()
    assert (vault / "AtomicX" / "Clients.md").is_file()
    assert (vault / "Projects" / "Inbox.md").is_file()

    overview = (vault / "AtomicX" / "Overview.md").read_text(encoding="utf-8")
    assert "tags: [atomicx, starter]" in overview

    cfg = persistence.load_connector_config("obsidian")
    assert cfg is not None
    assert cfg["vault_path"] == str(vault)


def test_ensure_default_vault_noop_when_obsidian_already_saved(
    redirected_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If an obsidian config already exists, no vault is created."""
    persistence.save_connector_config("obsidian", {"vault_path": "/elsewhere"})

    sentinel = tmp_path / "should-not-be-created"
    monkeypatch.setattr(persistence, "_default_vault_path", lambda: sentinel)

    result = persistence.ensure_default_vault()
    assert result is None
    assert not sentinel.exists()
