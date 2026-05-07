"""File sensitivity policy — block access to secrets, credentials, and keys."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Union

DEFAULT_SENSITIVE_PATTERNS: frozenset[str] = frozenset(
    {
        ".env",
        ".env.*",
        "*.env",
        ".secret",
        "*.secrets",
        "credentials.*",
        "*.pem",
        "*.key",
        "*.p12",
        "*.pfx",
        "*.jks",
        "id_rsa",
        "id_ed25519",
        ".htpasswd",
        ".pgpass",
        ".netrc",
    }
)


def is_sensitive_file(path: Union[str, Path]) -> bool:
    """Return ``True`` if *path* matches a sensitive file pattern.

    Checks both the filename and the full name against
    ``DEFAULT_SENSITIVE_PATTERNS`` using :func:`fnmatch.fnmatch`.
    Uses the Rust implementation when available, falls back to Python.
    """
    try:
        from openjarvis._rust_bridge import get_rust_module

        _rust = get_rust_module()
        return _rust.is_sensitive_file(str(path))
    except ImportError:
        return _is_sensitive_file_py(str(path))


def _is_sensitive_file_py(path_str: str) -> bool:
    """Pure-Python fallback for sensitive file detection."""
    import fnmatch

    p = Path(path_str)
    name = p.name
    for pattern in DEFAULT_SENSITIVE_PATTERNS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(str(p), pattern):
            return True
    return False


def filter_sensitive_paths(paths: Iterable[Union[str, Path]]) -> List[Path]:
    """Return only non-sensitive paths from *paths*."""
    return [Path(p) for p in paths if not is_sensitive_file(p)]


# ---------------------------------------------------------------------------
# Directory policy — MEM-4
# ---------------------------------------------------------------------------

#: Directories whose contents must NEVER be indexed. Always blocked.
SENSITIVE_DIRS: tuple[str, ...] = (
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.kube",
    "~/.docker",
    "~/Library/Keychains",
    "~/Library/Application Support/Google/Chrome",
    "~/Library/Application Support/Firefox",
    "~/AppData/Roaming/Mozilla",
    "~/AppData/Local/Google/Chrome/User Data",
    "~/AppData/Local/Microsoft/Edge/User Data",
    "~/.config/google-chrome",
    "~/.mozilla",
)

#: Directories that warrant a confirmation before indexing — typically too
#: broad but not inherently sensitive. Caller decides whether to proceed.
WARNING_DIRS: tuple[str, ...] = (
    "~",
    "~/Downloads",
    "~/Desktop",
    "~/Documents",
)


class PathRefused(Exception):
    """Raised when a path is inside a directory that must never be indexed."""

    def __init__(self, path: Path, sensitive_dir: str) -> None:
        self.path = path
        self.sensitive_dir = sensitive_dir
        super().__init__(
            f"{path} is inside a protected directory ({sensitive_dir}). "
            "Indexing this path is blocked."
        )


def _resolve(p: Union[str, Path]) -> Path:
    return Path(p).expanduser().resolve()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def is_within_sensitive_dir(path: Union[str, Path]) -> Optional[str]:
    """Return the matching entry from :data:`SENSITIVE_DIRS` if *path* is
    inside one (or equals one), else ``None``.
    """
    p = _resolve(path)
    for s in SENSITIVE_DIRS:
        s_path = _resolve(s)
        if p == s_path or _is_within(p, s_path):
            return s
    return None


def enforce_safe_path(path: Union[str, Path]) -> Path:
    """Resolve *path* and raise :class:`PathRefused` if it is inside a
    sensitive directory. Return the resolved :class:`Path` otherwise.
    """
    matched = is_within_sensitive_dir(path)
    if matched is not None:
        raise PathRefused(_resolve(path), matched)
    return _resolve(path)


def warning_for(path: Union[str, Path]) -> Optional[str]:
    """Return a warning message if *path* equals one of :data:`WARNING_DIRS`
    (broad/cautionary directories), else ``None``.

    Subpaths of a warning directory are not flagged — the warning is only
    for indexing the entire directory at its root.
    """
    p = _resolve(path)
    for w in WARNING_DIRS:
        if p == _resolve(w):
            return (
                f"You are about to index your entire {w} directory. "
                "This may include sensitive files."
            )
    return None


__all__ = [
    "DEFAULT_SENSITIVE_PATTERNS",
    "PathRefused",
    "SENSITIVE_DIRS",
    "WARNING_DIRS",
    "enforce_safe_path",
    "filter_sensitive_paths",
    "is_sensitive_file",
    "is_within_sensitive_dir",
    "warning_for",
]
