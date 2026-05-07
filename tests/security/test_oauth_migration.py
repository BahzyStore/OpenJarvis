"""Tests for the legacy-JSON-file → secrets_store migration in oauth.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjarvis.connectors import oauth
from openjarvis.security import secrets_store
from openjarvis.security.secrets_store import InMemoryBackend


@pytest.fixture(autouse=True)
def _isolated_backend() -> None:
    secrets_store._set_default_backend_for_tests(InMemoryBackend())
    yield
    secrets_store._set_default_backend_for_tests(None)


@pytest.fixture
def fake_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point :data:`oauth.DEFAULT_CONFIG_DIR` at a tmp dir."""
    monkeypatch.setattr(oauth, "DEFAULT_CONFIG_DIR", tmp_path)
    return tmp_path


class TestSaveLoadRoundTrip:
    def test_round_trip_via_secrets_store(self, fake_config_dir: Path) -> None:
        path = str(fake_config_dir / "connectors" / "google.json")
        tokens = {"access_token": "AT", "refresh_token": "RT"}
        oauth.save_tokens(path, tokens)
        assert oauth.load_tokens(path) == tokens

    def test_save_does_not_write_legacy_file(self, fake_config_dir: Path) -> None:
        path = fake_config_dir / "connectors" / "spotify.json"
        oauth.save_tokens(str(path), {"access_token": "AT"})
        # Tokens are in the store, not on disk.
        assert not path.exists()


class TestPathToKey:
    def test_under_config_dir(self, fake_config_dir: Path) -> None:
        key = oauth._path_to_key(str(fake_config_dir / "connectors" / "google.json"))
        assert key == "connectors/google"

    def test_outside_config_dir_uses_full_path(self) -> None:
        # An absolute path far away from the config dir uses its full path
        # so two unrelated files with the same stem do not collide.
        key = oauth._path_to_key("/tmp/somewhere/random.json")
        assert key == "tmp/somewhere/random"

    def test_two_outside_paths_with_same_stem_do_not_collide(self) -> None:
        a = oauth._path_to_key("/tmp/run-a/outlook.json")
        b = oauth._path_to_key("/tmp/run-b/outlook.json")
        assert a != b

    def test_nested_subdirs(self, fake_config_dir: Path) -> None:
        key = oauth._path_to_key(str(fake_config_dir / "deep" / "nested" / "path.json"))
        assert key == "deep/nested/path"


class TestLegacyMigration:
    def test_load_migrates_legacy_json(self, fake_config_dir: Path) -> None:
        path = fake_config_dir / "connectors" / "google.json"
        path.parent.mkdir(parents=True)
        legacy = {"access_token": "legacy_AT", "refresh_token": "legacy_RT"}
        path.write_text(json.dumps(legacy), encoding="utf-8")

        # First load reads from disk and migrates.
        loaded = oauth.load_tokens(str(path))
        assert loaded == legacy

        # Original file is renamed.
        assert not path.exists()
        assert path.with_suffix(".json.legacy").exists()

        # Subsequent load comes from the store, not disk.
        again = oauth.load_tokens(str(path))
        assert again == legacy

    def test_corrupt_legacy_json_returns_none(self, fake_config_dir: Path) -> None:
        path = fake_config_dir / "connectors" / "google.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert oauth.load_tokens(str(path)) is None
        # Nothing was migrated to the store.
        assert oauth.load_tokens(str(path)) is None


class TestDelete:
    def test_delete_removes_store_entry_and_legacy_file(
        self, fake_config_dir: Path
    ) -> None:
        path = fake_config_dir / "connectors" / "spotify.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"access_token": "X"}), encoding="utf-8")
        # Trigger migration.
        oauth.load_tokens(str(path))
        legacy = path.with_suffix(".json.legacy")
        assert legacy.exists()

        oauth.delete_tokens(str(path))
        assert oauth.load_tokens(str(path)) is None
        assert not legacy.exists()

    def test_delete_when_only_in_store(self, fake_config_dir: Path) -> None:
        path = str(fake_config_dir / "connectors" / "spotify.json")
        oauth.save_tokens(path, {"access_token": "X"})
        oauth.delete_tokens(path)
        assert oauth.load_tokens(path) is None

    def test_delete_missing_is_noop(self, fake_config_dir: Path) -> None:
        path = str(fake_config_dir / "connectors" / "missing.json")
        # Must not raise.
        oauth.delete_tokens(path)


class TestLoadMissing:
    def test_load_missing_returns_none(self, fake_config_dir: Path) -> None:
        path = str(fake_config_dir / "connectors" / "nothing.json")
        assert oauth.load_tokens(path) is None
