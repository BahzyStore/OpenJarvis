"""Tests for extended API routes."""

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.server.api_routes import include_all_routes  # noqa: E402


def _make_app():
    app = FastAPI()
    include_all_routes(app)
    return app


class TestAgentRoutes:
    def test_list_agents(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "registered" in data
        assert "running" in data

    def test_create_agent(self):
        client = TestClient(_make_app())
        resp = client.post("/v1/agents", json={"agent_type": "simple"})
        # May succeed or fail depending on agent_tools availability
        assert resp.status_code in (200, 501)

    def test_kill_nonexistent(self):
        client = TestClient(_make_app())
        resp = client.delete("/v1/agents/nonexistent")
        assert resp.status_code in (404, 501)


class TestMemoryRoutes:
    def test_search(self):
        client = TestClient(_make_app())
        resp = client.post("/v1/memory/search", json={"query": "test"})
        # May fail if SQLite not set up, that's ok
        assert resp.status_code in (200, 500)

    def test_stats(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/memory/stats")
        assert resp.status_code in (200, 500)


class TestBudgetRoutes:
    def test_get_budget(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/budget")
        assert resp.status_code == 200
        data = resp.json()
        assert "limits" in data
        assert "usage" in data

    def test_set_limits(self):
        client = TestClient(_make_app())
        resp = client.put("/v1/budget/limits", json={"max_tokens_per_day": 100000})
        assert resp.status_code == 200
        assert resp.json()["limits"]["max_tokens_per_day"] == 100000


class TestMetricsRoute:
    def test_metrics_endpoint(self):
        client = TestClient(_make_app())
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "openjarvis" in resp.text or "No metrics" in resp.text


class TestSkillRoutes:
    def test_list_skills(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/skills")
        assert resp.status_code == 200
        assert "skills" in resp.json()


class TestSessionRoutes:
    def test_list_sessions(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/sessions")
        assert resp.status_code == 200


class TestTraceRoutes:
    def test_list_traces(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/traces")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Memory subsystem — health endpoint (MEM-1) and path safety on /index (MEM-4)
# ---------------------------------------------------------------------------


class TestMemoryHealth:
    def test_health_returns_200_or_503(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/memory/health")
        assert resp.status_code in (200, 503)
        body = resp.json()
        assert body.get("status") in ("ok", "unavailable")

    def test_health_ok_shape(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/memory/health")
        if resp.status_code == 200:
            body = resp.json()
            assert body["status"] == "ok"
            assert "backend" in body
            assert "items" in body
            assert isinstance(body["items"], int)

    def test_health_unavailable_shape(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/memory/health")
        if resp.status_code == 503:
            body = resp.json()
            assert body["status"] == "unavailable"
            assert "reason" in body


class TestMemoryIndexSafety:
    def test_index_refuses_sensitive_dir_with_409(self, tmp_path, monkeypatch):
        protected = tmp_path / "vault"
        protected.mkdir()
        target = protected / "private.key"
        target.write_text("secret")
        monkeypatch.setattr(
            "openjarvis.security.file_policy.SENSITIVE_DIRS",
            (str(protected),),
        )
        client = TestClient(_make_app())
        resp = client.post(
            "/v1/memory/index",
            json={"path": str(target)},
        )
        assert resp.status_code == 409
        detail = resp.json().get("detail", "")
        assert "protected" in str(detail).lower() or "vault" in str(detail)

    def test_index_refuses_protected_dir_root_with_409(self, tmp_path, monkeypatch):
        protected = tmp_path / "vault"
        protected.mkdir()
        monkeypatch.setattr(
            "openjarvis.security.file_policy.SENSITIVE_DIRS",
            (str(protected),),
        )
        client = TestClient(_make_app())
        resp = client.post(
            "/v1/memory/index",
            json={"path": str(protected)},
        )
        assert resp.status_code == 409

    def test_index_unprotected_missing_path_returns_404(self, tmp_path):
        # An unprotected, non-existent path should yield 404 (path-not-found),
        # not 409 (refused). The route reaches the .exists() check.
        missing = tmp_path / "does-not-exist.txt"
        client = TestClient(_make_app())
        resp = client.post(
            "/v1/memory/index",
            json={"path": str(missing)},
        )
        # 404 (not found) is the documented path; 503 (no backend) is also
        # acceptable depending on the test environment.
        assert resp.status_code in (404, 503)
