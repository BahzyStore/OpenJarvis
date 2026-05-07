"""Tests for file sensitivity policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.security.file_policy import (
    PathRefused,
    enforce_safe_path,
    filter_sensitive_paths,
    is_sensitive_file,
    is_within_sensitive_dir,
    warning_for,
)


class TestIsSensitiveFile:
    def test_sensitive_env(self) -> None:
        assert is_sensitive_file(".env") is True

    def test_sensitive_env_local(self) -> None:
        assert is_sensitive_file(".env.local") is True

    def test_sensitive_pem(self) -> None:
        assert is_sensitive_file("server.pem") is True

    def test_sensitive_key(self) -> None:
        assert is_sensitive_file("private.key") is True

    def test_sensitive_id_rsa(self) -> None:
        assert is_sensitive_file("id_rsa") is True

    def test_sensitive_credentials(self) -> None:
        assert is_sensitive_file("credentials.json") is True

    def test_sensitive_htpasswd(self) -> None:
        assert is_sensitive_file(".htpasswd") is True

    def test_sensitive_pgpass(self) -> None:
        assert is_sensitive_file(".pgpass") is True

    def test_sensitive_netrc(self) -> None:
        assert is_sensitive_file(".netrc") is True

    def test_sensitive_p12(self) -> None:
        assert is_sensitive_file("cert.p12") is True

    def test_sensitive_pfx(self) -> None:
        assert is_sensitive_file("cert.pfx") is True

    def test_sensitive_jks(self) -> None:
        assert is_sensitive_file("keystore.jks") is True

    def test_sensitive_id_ed25519(self) -> None:
        assert is_sensitive_file("id_ed25519") is True

    def test_sensitive_secret(self) -> None:
        assert is_sensitive_file(".secret") is True

    def test_sensitive_env_in_path(self) -> None:
        assert is_sensitive_file(Path("/some/dir/.env")) is True

    def test_not_sensitive_py(self) -> None:
        assert is_sensitive_file("main.py") is False

    def test_not_sensitive_txt(self) -> None:
        assert is_sensitive_file("readme.txt") is False

    def test_not_sensitive_toml(self) -> None:
        assert is_sensitive_file("pyproject.toml") is False

    def test_not_sensitive_json(self) -> None:
        assert is_sensitive_file("package.json") is False

    def test_path_object(self) -> None:
        assert is_sensitive_file(Path("server.pem")) is True
        assert is_sensitive_file(Path("main.py")) is False


class TestFilterSensitivePaths:
    def test_filter_sensitive_paths(self) -> None:
        paths = [
            "main.py",
            ".env",
            "server.pem",
            "readme.txt",
            "credentials.json",
            "app.js",
        ]
        filtered = filter_sensitive_paths(paths)
        names = [p.name for p in filtered]
        assert "main.py" in names
        assert "readme.txt" in names
        assert "app.js" in names
        assert ".env" not in names
        assert "server.pem" not in names
        assert "credentials.json" not in names

    def test_filter_all_sensitive(self) -> None:
        paths = [".env", "server.pem", "credentials.json"]
        filtered = filter_sensitive_paths(paths)
        assert filtered == []

    def test_filter_none_sensitive(self) -> None:
        paths = ["main.py", "app.js", "readme.txt"]
        filtered = filter_sensitive_paths(paths)
        assert len(filtered) == 3

    def test_filter_empty(self) -> None:
        assert filter_sensitive_paths([]) == []


# ---------------------------------------------------------------------------
# Directory-policy tests (MEM-4)
# ---------------------------------------------------------------------------


class TestPathRefusedException:
    def test_carries_offending_path_and_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "secret.txt"
        exc = PathRefused(target, "~/.ssh")
        assert exc.path == target
        assert exc.sensitive_dir == "~/.ssh"
        assert "protected" in str(exc)
        assert "~/.ssh" in str(exc)


class TestIsWithinSensitiveDir:
    def test_path_at_protected_root_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        protected = tmp_path / "vault"
        protected.mkdir()
        monkeypatch.setattr(
            "openjarvis.security.file_policy.SENSITIVE_DIRS",
            (str(protected),),
        )
        assert is_within_sensitive_dir(protected) == str(protected)

    def test_path_inside_protected_dir_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        protected = tmp_path / "vault"
        protected.mkdir()
        nested = protected / "deep" / "secret.txt"
        nested.parent.mkdir()
        nested.write_text("x")
        monkeypatch.setattr(
            "openjarvis.security.file_policy.SENSITIVE_DIRS",
            (str(protected),),
        )
        assert is_within_sensitive_dir(nested) == str(protected)

    def test_unprotected_path_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        protected = tmp_path / "vault"
        protected.mkdir()
        outside = tmp_path / "other.txt"
        outside.write_text("x")
        monkeypatch.setattr(
            "openjarvis.security.file_policy.SENSITIVE_DIRS",
            (str(protected),),
        )
        assert is_within_sensitive_dir(outside) is None


class TestEnforceSafePath:
    def test_inside_protected_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        protected = tmp_path / "vault"
        protected.mkdir()
        target = protected / "private.key"
        target.write_text("contents")
        monkeypatch.setattr(
            "openjarvis.security.file_policy.SENSITIVE_DIRS",
            (str(protected),),
        )
        with pytest.raises(PathRefused) as info:
            enforce_safe_path(target)
        assert info.value.sensitive_dir == str(protected)

    def test_at_protected_root_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        protected = tmp_path / "vault"
        protected.mkdir()
        monkeypatch.setattr(
            "openjarvis.security.file_policy.SENSITIVE_DIRS",
            (str(protected),),
        )
        with pytest.raises(PathRefused):
            enforce_safe_path(protected)

    def test_unprotected_returns_resolved_path(self, tmp_path: Path) -> None:
        target = tmp_path / "ok.txt"
        target.write_text("ok")
        result = enforce_safe_path(target)
        assert result == target.resolve()

    def test_nonexistent_path_still_evaluated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even a not-yet-existent path under a protected dir is refused.
        protected = tmp_path / "vault"
        protected.mkdir()
        monkeypatch.setattr(
            "openjarvis.security.file_policy.SENSITIVE_DIRS",
            (str(protected),),
        )
        with pytest.raises(PathRefused):
            enforce_safe_path(protected / "future.key")


class TestWarningFor:
    def test_warning_dir_root_returns_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        downloads = tmp_path / "Downloads"
        downloads.mkdir()
        monkeypatch.setattr(
            "openjarvis.security.file_policy.WARNING_DIRS",
            (str(downloads),),
        )
        msg = warning_for(downloads)
        assert msg is not None
        assert "Downloads" in msg

    def test_subpath_of_warning_dir_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        downloads = tmp_path / "Downloads"
        downloads.mkdir()
        nested = downloads / "file.txt"
        nested.write_text("x")
        monkeypatch.setattr(
            "openjarvis.security.file_policy.WARNING_DIRS",
            (str(downloads),),
        )
        # warning_for fires only on the dir root, not children
        assert warning_for(nested) is None

    def test_unrelated_path_returns_none(self, tmp_path: Path) -> None:
        assert warning_for(tmp_path) is None


class TestBuiltinSensitiveDirsList:
    """Sanity checks: the shipped SENSITIVE_DIRS / WARNING_DIRS are non-empty
    and include the most-critical entries."""

    def test_ssh_in_sensitive(self) -> None:
        from openjarvis.security.file_policy import SENSITIVE_DIRS

        assert any(".ssh" in s for s in SENSITIVE_DIRS)

    def test_aws_in_sensitive(self) -> None:
        from openjarvis.security.file_policy import SENSITIVE_DIRS

        assert any(".aws" in s for s in SENSITIVE_DIRS)

    def test_gnupg_in_sensitive(self) -> None:
        from openjarvis.security.file_policy import SENSITIVE_DIRS

        assert any(".gnupg" in s for s in SENSITIVE_DIRS)

    def test_warning_includes_home_and_downloads(self) -> None:
        from openjarvis.security.file_policy import WARNING_DIRS

        assert "~" in WARNING_DIRS
        assert any("Downloads" in w for w in WARNING_DIRS)
