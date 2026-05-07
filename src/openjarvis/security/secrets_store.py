"""OS-keychain-backed credential storage with file fallback.

This module is the single source of truth for credential storage in
openjarvis: connector OAuth tokens, integration tokens, and any future API
keys live here, never in plaintext config files.

Public API
----------
``set_secret(name, value)`` / ``get_secret(name)`` / ``delete_secret(name)`` /
``list_secret_names()`` — all delegate to the active backend.

Backends
--------
The default backend is selected lazily on first use:

* :class:`KeyringBackend` — wraps the third-party ``keyring`` library, which
  bridges to DPAPI on Windows, Keychain Access on macOS, and the Secret
  Service / libsecret on Linux desktops. **Default when available.**
* :class:`FileBackend` — stores each secret as a JSON file (mode 0o600) under
  ``$DEFAULT_CONFIG_DIR/secrets/``. Used as fallback on headless servers,
  Docker images, and WSL distributions without a Secret Service daemon.
* :class:`InMemoryBackend` — for tests.

The file fallback is still safer than the prior behavior (per-connector
plaintext JSON scattered across ``~/.openjarvis/connectors/``) because it
lives in one well-known location and a future change can swap the on-disk
format for one encrypted with a passphrase or app-bound key.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from openjarvis.core.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

#: Service name namespacing all openjarvis entries inside the OS keyring.
SERVICE_NAME = "openjarvis"

_DEFAULT_DIR = DEFAULT_CONFIG_DIR / "secrets"


@runtime_checkable
class SecretsBackend(Protocol):
    """Pluggable storage backend for openjarvis credentials."""

    def set(self, name: str, value: str) -> None: ...
    def get(self, name: str) -> Optional[str]: ...
    def delete(self, name: str) -> None: ...
    def list_names(self) -> list[str]: ...


class KeyringBackend:
    """Stores secrets in the OS keyring via the ``keyring`` package."""

    def __init__(self, service: str = SERVICE_NAME) -> None:
        import keyring  # lazy: only loaded when this backend is used

        self._keyring = keyring
        self._service = service

    def set(self, name: str, value: str) -> None:
        self._keyring.set_password(self._service, name, value)

    def get(self, name: str) -> Optional[str]:
        return self._keyring.get_password(self._service, name)

    def delete(self, name: str) -> None:
        try:
            self._keyring.delete_password(self._service, name)
        except Exception:
            # delete_password raises on missing on some backends; treat as no-op.
            pass

    def list_names(self) -> list[str]:
        # The ``keyring`` lib does not expose a portable enumeration API.
        # Returning [] is the honest answer; callers needing enumeration
        # should keep their own index or use FileBackend.
        return []


class FileBackend:
    """JSON-per-secret with mode 0o600 — last-resort fallback."""

    def __init__(self, dir_: Optional[Path] = None) -> None:
        self._dir = Path(dir_) if dir_ is not None else _DEFAULT_DIR

    def _path(self, name: str) -> Path:
        # Forward slashes in *name* map to subdirs (e.g. "connectors/google").
        return self._dir / f"{name}.json"

    def set(self, name: str, value: str) -> None:
        p = self._path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"value": value}), encoding="utf-8")
        try:
            os.chmod(p, 0o600)
        except OSError:
            # Windows ignores POSIX mode bits; that's fine.
            pass

    def get(self, name: str) -> Optional[str]:
        p = self._path(name)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        value = data.get("value")
        return value if isinstance(value, str) else None

    def delete(self, name: str) -> None:
        p = self._path(name)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    def list_names(self) -> list[str]:
        if not self._dir.exists():
            return []
        names: list[str] = []
        for p in self._dir.rglob("*.json"):
            rel = p.relative_to(self._dir).with_suffix("")
            names.append(str(rel).replace(os.sep, "/"))
        return sorted(names)


class InMemoryBackend:
    """Volatile, process-local backend — for tests and CI."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set(self, name: str, value: str) -> None:
        self._store[name] = value

    def get(self, name: str) -> Optional[str]:
        return self._store.get(name)

    def delete(self, name: str) -> None:
        self._store.pop(name, None)

    def list_names(self) -> list[str]:
        return sorted(self._store)


def _build_default_backend() -> SecretsBackend:
    """Pick the best available backend at first use.

    Tries :class:`KeyringBackend` and probes it with a short round-trip.
    Falls back to :class:`FileBackend` on any failure (no usable keyring,
    keyring lib not installed, or an environment without a Secret Service).
    """
    try:
        be = KeyringBackend()
        probe = "_openjarvis_keyring_probe_"
        be.set(probe, "ok")
        try:
            ok = be.get(probe) == "ok"
        finally:
            be.delete(probe)
        if ok:
            return be
        logger.warning("Keyring probe failed (set/get mismatch); using FileBackend.")
    except Exception as e:
        logger.warning(
            "OS keyring unavailable (%s: %s). Falling back to FileBackend at %s.",
            type(e).__name__,
            e,
            _DEFAULT_DIR,
        )
    return FileBackend()


_default: Optional[SecretsBackend] = None


def _backend() -> SecretsBackend:
    global _default
    if _default is None:
        _default = _build_default_backend()
    return _default


def _set_default_backend_for_tests(backend: Optional[SecretsBackend]) -> None:
    """Test-only: replace the lazily-built singleton (or reset with ``None``)."""
    global _default
    _default = backend


# --- Public API ---


def set_secret(name: str, value: str) -> None:
    """Store *value* under *name* in the active backend."""
    _backend().set(name, value)


def get_secret(name: str) -> Optional[str]:
    """Return the value stored under *name*, or ``None`` if absent."""
    return _backend().get(name)


def delete_secret(name: str) -> None:
    """Remove the secret stored under *name* (no-op if absent)."""
    _backend().delete(name)


def list_secret_names() -> list[str]:
    """List names of stored secrets.

    May return an empty list when the active backend does not support
    enumeration (e.g. :class:`KeyringBackend`).
    """
    return _backend().list_names()
