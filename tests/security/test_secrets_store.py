"""Tests for openjarvis.security.secrets_store."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openjarvis.security import secrets_store
from openjarvis.security.secrets_store import (
    FileBackend,
    InMemoryBackend,
    KeyringBackend,
    SecretsBackend,
)


@pytest.fixture(autouse=True)
def _isolated_in_memory_backend() -> None:
    """Each test gets a fresh InMemoryBackend; reset afterwards."""
    secrets_store._set_default_backend_for_tests(InMemoryBackend())
    yield
    secrets_store._set_default_backend_for_tests(None)


class TestPublicAPI:
    def test_set_and_get_round_trip(self) -> None:
        secrets_store.set_secret("foo", "bar")
        assert secrets_store.get_secret("foo") == "bar"

    def test_get_missing_returns_none(self) -> None:
        assert secrets_store.get_secret("nope") is None

    def test_overwrite_replaces_value(self) -> None:
        secrets_store.set_secret("k", "v1")
        secrets_store.set_secret("k", "v2")
        assert secrets_store.get_secret("k") == "v2"

    def test_delete_removes_value(self) -> None:
        secrets_store.set_secret("k", "v")
        secrets_store.delete_secret("k")
        assert secrets_store.get_secret("k") is None

    def test_delete_missing_is_noop(self) -> None:
        # Must not raise.
        secrets_store.delete_secret("never_existed")

    def test_list_names_includes_set_keys(self) -> None:
        secrets_store.set_secret("a", "1")
        secrets_store.set_secret("b/c", "2")
        names = secrets_store.list_secret_names()
        assert "a" in names
        assert "b/c" in names


class TestInMemoryBackend:
    def test_round_trip(self) -> None:
        be = InMemoryBackend()
        be.set("k", "v")
        assert be.get("k") == "v"
        be.delete("k")
        assert be.get("k") is None

    def test_list_names_sorted(self) -> None:
        be = InMemoryBackend()
        be.set("z", "1")
        be.set("a", "2")
        be.set("m", "3")
        assert be.list_names() == ["a", "m", "z"]


class TestFileBackend:
    def test_round_trip(self, tmp_path: Path) -> None:
        be = FileBackend(tmp_path)
        be.set("foo", "bar")
        assert be.get("foo") == "bar"

    def test_nested_keys_create_subdirs(self, tmp_path: Path) -> None:
        be = FileBackend(tmp_path)
        be.set("connectors/google", "tok")
        assert (tmp_path / "connectors" / "google.json").exists()
        assert be.get("connectors/google") == "tok"

    def test_file_permissions_owner_only(self, tmp_path: Path) -> None:
        be = FileBackend(tmp_path)
        be.set("k", "v")
        f = tmp_path / "k.json"
        # POSIX-only assertion; Windows ignores mode bits and that's OK.
        if os.name == "posix":
            assert f.stat().st_mode & 0o777 == 0o600

    def test_delete_removes_file(self, tmp_path: Path) -> None:
        be = FileBackend(tmp_path)
        be.set("k", "v")
        be.delete("k")
        assert be.get("k") is None
        assert not (tmp_path / "k.json").exists()

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        be = FileBackend(tmp_path)
        assert be.get("never") is None

    def test_corrupt_file_returns_none(self, tmp_path: Path) -> None:
        be = FileBackend(tmp_path)
        # Write malformed JSON directly.
        (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
        assert be.get("bad") is None

    def test_list_names_returns_all(self, tmp_path: Path) -> None:
        be = FileBackend(tmp_path)
        be.set("a", "1")
        be.set("b/c", "2")
        be.set("b/d", "3")
        assert be.list_names() == ["a", "b/c", "b/d"]


class TestKeyringBackend:
    """Verify KeyringBackend dispatches to the keyring lib correctly."""

    def _install_fake_keyring(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        fake = MagicMock()
        monkeypatch.setitem(sys.modules, "keyring", fake)
        return fake

    def test_set_calls_set_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = self._install_fake_keyring(monkeypatch)
        be = KeyringBackend("test-svc")
        be.set("k", "v")
        fake.set_password.assert_called_once_with("test-svc", "k", "v")

    def test_get_returns_value_from_get_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = self._install_fake_keyring(monkeypatch)
        fake.get_password.return_value = "the-value"
        be = KeyringBackend("test-svc")
        assert be.get("k") == "the-value"
        fake.get_password.assert_called_once_with("test-svc", "k")

    def test_get_returns_none_when_keyring_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = self._install_fake_keyring(monkeypatch)
        fake.get_password.return_value = None
        be = KeyringBackend("test-svc")
        assert be.get("missing") is None

    def test_delete_calls_delete_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = self._install_fake_keyring(monkeypatch)
        be = KeyringBackend("test-svc")
        be.delete("k")
        fake.delete_password.assert_called_once_with("test-svc", "k")

    def test_delete_swallows_exceptions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = self._install_fake_keyring(monkeypatch)
        fake.delete_password.side_effect = RuntimeError("missing")
        be = KeyringBackend("test-svc")
        # Must not raise.
        be.delete("k")

    def test_list_names_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._install_fake_keyring(monkeypatch)
        be = KeyringBackend("test-svc")
        # keyring lib has no portable enumeration -> empty list is the contract.
        assert be.list_names() == []


class TestProtocolConformance:
    """All concrete backends must satisfy the SecretsBackend protocol."""

    def test_in_memory_backend_conforms(self) -> None:
        assert isinstance(InMemoryBackend(), SecretsBackend)

    def test_file_backend_conforms(self, tmp_path: Path) -> None:
        assert isinstance(FileBackend(tmp_path), SecretsBackend)
