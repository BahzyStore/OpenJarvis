"""Tests for Agent Manager API routes."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openjarvis.agents.manager import AgentManager


@pytest.fixture
def manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = AgentManager(db_path=str(Path(tmpdir) / "agents.db"))
        yield mgr
        mgr.close()


try:
    from fastapi.testclient import TestClient

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


@pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")
class TestAgentManagerRoutes:
    @pytest.fixture
    def client(self, manager):
        from fastapi import FastAPI

        from openjarvis.server.agent_manager_routes import create_agent_manager_router

        app = FastAPI()
        routers = create_agent_manager_router(manager)
        # create_agent_manager_router returns 5 routers since sendblue was
        # added; the fixture unpacks all of them defensively.
        for r in routers:
            app.include_router(r)
        return TestClient(app)

    def test_list_agents_empty(self, client):
        resp = client.get("/v1/managed-agents")
        assert resp.status_code == 200
        assert resp.json()["agents"] == []

    def test_create_agent(self, client):
        resp = client.post(
            "/v1/managed-agents",
            json={
                "name": "researcher",
                "agent_type": "monitor_operative",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "researcher"
        assert data["status"] == "idle"

    def test_get_agent(self, client):
        create_resp = client.post("/v1/managed-agents", json={"name": "test"})
        agent_id = create_resp.json()["id"]
        resp = client.get(f"/v1/managed-agents/{agent_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == agent_id

    def test_get_agent_not_found(self, client):
        resp = client.get("/v1/managed-agents/nonexistent")
        assert resp.status_code == 404

    def test_update_agent(self, client):
        create_resp = client.post("/v1/managed-agents", json={"name": "old"})
        agent_id = create_resp.json()["id"]
        resp = client.patch(f"/v1/managed-agents/{agent_id}", json={"name": "new"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "new"

    def test_delete_agent(self, client):
        create_resp = client.post("/v1/managed-agents", json={"name": "doomed"})
        agent_id = create_resp.json()["id"]
        resp = client.delete(f"/v1/managed-agents/{agent_id}")
        assert resp.status_code == 200

    def test_pause_resume(self, client):
        create_resp = client.post("/v1/managed-agents", json={"name": "pausable"})
        agent_id = create_resp.json()["id"]
        client.post(f"/v1/managed-agents/{agent_id}/pause")
        resp = client.get(f"/v1/managed-agents/{agent_id}")
        assert resp.json()["status"] == "paused"
        client.post(f"/v1/managed-agents/{agent_id}/resume")
        resp = client.get(f"/v1/managed-agents/{agent_id}")
        assert resp.json()["status"] == "idle"

    def test_create_task(self, client):
        create_resp = client.post("/v1/managed-agents", json={"name": "worker"})
        agent_id = create_resp.json()["id"]
        resp = client.post(
            f"/v1/managed-agents/{agent_id}/tasks",
            json={
                "description": "Find papers on reasoning",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Find papers on reasoning"

    def test_list_tasks(self, client):
        create_resp = client.post("/v1/managed-agents", json={"name": "worker"})
        agent_id = create_resp.json()["id"]
        client.post(f"/v1/managed-agents/{agent_id}/tasks", json={"description": "t1"})
        client.post(f"/v1/managed-agents/{agent_id}/tasks", json={"description": "t2"})
        resp = client.get(f"/v1/managed-agents/{agent_id}/tasks")
        assert len(resp.json()["tasks"]) == 2

    def test_channel_binding_crud(self, client):
        create_resp = client.post("/v1/managed-agents", json={"name": "slacker"})
        agent_id = create_resp.json()["id"]
        # Bind
        bind_resp = client.post(
            f"/v1/managed-agents/{agent_id}/channels",
            json={
                "channel_type": "slack",
                "config": {"channel": "#research"},
            },
        )
        assert bind_resp.status_code == 200
        binding_id = bind_resp.json()["id"]
        # List
        list_resp = client.get(f"/v1/managed-agents/{agent_id}/channels")
        assert len(list_resp.json()["bindings"]) == 1
        # Unbind
        url = f"/v1/managed-agents/{agent_id}/channels/{binding_id}"
        unbind_resp = client.delete(url)
        assert unbind_resp.status_code == 200

    def test_templates(self, client):
        resp = client.get("/v1/templates")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        assert any(t["id"] == "research_monitor" for t in templates)

    def test_recover_agent(self, manager, client):
        # Create agent, save checkpoint, set error status
        agent = manager.create_agent(name="err", agent_type="simple")
        manager.save_checkpoint(agent["id"], "tick-1", {"msgs": []}, {})
        manager.update_agent(agent["id"], status="error")

        res = client.post(f"/v1/managed-agents/{agent['id']}/recover")
        assert res.status_code == 200
        body = res.json()
        assert body["recovered"] is True
        assert body["checkpoint"]["tick_id"] == "tick-1"

    def test_recover_agent_no_checkpoint(self, manager, client):
        agent = manager.create_agent(name="err", agent_type="simple")
        manager.update_agent(agent["id"], status="error")
        res = client.post(f"/v1/managed-agents/{agent['id']}/recover")
        assert res.status_code == 200
        body = res.json()
        assert body["recovered"] is True
        assert body["checkpoint"] is None
        # Status should be reset to idle
        refreshed = manager.get_agent(agent["id"])
        assert refreshed["status"] == "idle"

    def test_list_error_agents(self, manager, client):
        manager.create_agent(name="ok", agent_type="simple")
        err = manager.create_agent(name="broken", agent_type="simple")
        manager.update_agent(err["id"], status="error")

        res = client.get("/v1/agents/errors")
        assert res.status_code == 200
        agents = res.json()["agents"]
        assert len(agents) == 1
        assert agents[0]["name"] == "broken"

    def test_send_and_list_messages(self, manager, client):
        agent = manager.create_agent(name="chat", agent_type="simple")

        res = client.post(
            f"/v1/managed-agents/{agent['id']}/messages",
            json={"content": "hello", "mode": "queued"},
        )
        assert res.status_code == 200

        res = client.get(f"/v1/managed-agents/{agent['id']}/messages")
        assert res.status_code == 200
        assert len(res.json()["messages"]) == 1

    def test_get_agent_state(self, manager, client):
        agent = manager.create_agent(name="stateful", agent_type="simple")
        res = client.get(f"/v1/managed-agents/{agent['id']}/state")
        assert res.status_code == 200
        state = res.json()
        assert "agent" in state
        assert "tasks" in state
        assert "channels" in state
        assert "messages" in state
        assert "checkpoint" in state

    def test_send_message_non_stream_unchanged(self, manager, client):
        """stream=False (default) returns a normal JSON message, not SSE."""
        agent = manager.create_agent(name="basic", agent_type="simple")
        res = client.post(
            f"/v1/managed-agents/{agent['id']}/messages",
            json={"content": "hello", "stream": False},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["content"] == "hello"
        assert data["direction"] == "user_to_agent"

    def test_send_message_stream_not_found(self, manager, client):
        """Streaming to a non-existent agent returns 404."""
        res = client.post(
            "/v1/managed-agents/nonexistent/messages",
            json={"content": "hello", "stream": True},
        )
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# Per-agent data-source allowlist (AG-9)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")
class TestAgentAccess:
    """Phase 5 — GET/PATCH /v1/managed-agents/{id}/access plus the
    default-deny `allowed_data_sources: []` applied at create."""

    @pytest.fixture
    def client(self, manager):
        from fastapi import FastAPI

        from openjarvis.core.registry import ConnectorRegistry
        from openjarvis.server.agent_manager_routes import create_agent_manager_router

        # The autouse `_clean_registries` fixture in tests/conftest.py wipes
        # ConnectorRegistry before each test, so pre-populate it with a few
        # placeholder connectors. This isolates the test from the real
        # connector inventory while still exercising the registry-validation
        # path in PATCH /access.
        for cid in ("test_alpha", "test_beta", "test_gamma"):
            ConnectorRegistry.register(cid)(object)

        app = FastAPI()
        for r in create_agent_manager_router(manager):
            app.include_router(r)
        return TestClient(app)

    def test_default_allowlist_is_empty_at_create(self, client):
        agent_id = client.post("/v1/managed-agents", json={"name": "a"}).json()["id"]
        body = client.get(f"/v1/managed-agents/{agent_id}/access").json()
        assert body["allowed_data_sources"] == []
        assert body["wildcard"] is False

    def test_get_includes_available_connectors(self, client):
        agent_id = client.post("/v1/managed-agents", json={"name": "a"}).json()["id"]
        body = client.get(f"/v1/managed-agents/{agent_id}/access").json()
        assert "available_data_sources" in body
        assert isinstance(body["available_data_sources"], list)
        assert len(body["available_data_sources"]) > 0
        assert all(isinstance(s, str) for s in body["available_data_sources"])

    def test_get_404_for_unknown_agent(self, client):
        assert client.get("/v1/managed-agents/nope/access").status_code == 404

    def test_patch_grants_known_connectors(self, client):
        agent_id = client.post("/v1/managed-agents", json={"name": "a"}).json()["id"]
        # Use connector IDs from the registry to avoid hardcoding names
        # that might be removed/renamed in the future.
        available = client.get(f"/v1/managed-agents/{agent_id}/access").json()[
            "available_data_sources"
        ]
        assert len(available) >= 2
        first_two = available[:2]
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/access",
            json={"allowed_data_sources": first_two},
        )
        assert resp.status_code == 200
        assert resp.json()["allowed_data_sources"] == first_two
        assert resp.json()["wildcard"] is False

    def test_patch_wildcard_grants_all(self, client):
        agent_id = client.post("/v1/managed-agents", json={"name": "a"}).json()["id"]
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/access",
            json={"allowed_data_sources": ["*"]},
        )
        assert resp.status_code == 200
        assert resp.json()["allowed_data_sources"] == ["*"]
        assert resp.json()["wildcard"] is True

    def test_patch_unknown_connector_returns_422(self, client):
        agent_id = client.post("/v1/managed-agents", json={"name": "a"}).json()["id"]
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/access",
            json={"allowed_data_sources": ["gmial", "not-a-real-connector"]},
        )
        assert resp.status_code == 422
        detail = str(resp.json().get("detail", "")).lower()
        assert "unknown" in detail or "gmial" in detail
        # And the agent's allowlist was NOT mutated.
        body = client.get(f"/v1/managed-agents/{agent_id}/access").json()
        assert body["allowed_data_sources"] == []

    def test_patch_mixed_known_and_unknown_returns_422(self, client):
        agent_id = client.post("/v1/managed-agents", json={"name": "a"}).json()["id"]
        available = client.get(f"/v1/managed-agents/{agent_id}/access").json()[
            "available_data_sources"
        ]
        assert len(available) >= 1
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/access",
            json={"allowed_data_sources": [available[0], "definitely-not-real"]},
        )
        # All-or-nothing: any unknown rejects the whole patch.
        assert resp.status_code == 422

    def test_patch_empty_list_resets_to_deny_all(self, client):
        agent_id = client.post("/v1/managed-agents", json={"name": "a"}).json()["id"]
        client.patch(
            f"/v1/managed-agents/{agent_id}/access",
            json={"allowed_data_sources": ["*"]},
        )
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/access",
            json={"allowed_data_sources": []},
        )
        assert resp.status_code == 200
        assert resp.json()["allowed_data_sources"] == []
        assert resp.json()["wildcard"] is False

    def test_patch_dedupes_repeated_entries(self, client):
        agent_id = client.post("/v1/managed-agents", json={"name": "a"}).json()["id"]
        available = client.get(f"/v1/managed-agents/{agent_id}/access").json()[
            "available_data_sources"
        ]
        first = available[0]
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/access",
            json={"allowed_data_sources": [first, first, "*", "*"]},
        )
        assert resp.status_code == 200
        # Order preserved, duplicates removed.
        assert resp.json()["allowed_data_sources"] == [first, "*"]

    def test_patch_404_for_unknown_agent(self, client):
        resp = client.patch(
            "/v1/managed-agents/nope/access",
            json={"allowed_data_sources": ["*"]},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Per-channel-binding inbound sender allowlist (MC-4)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")
class TestChannelBindingAccess:
    """Phase 7 — GET/PATCH /v1/managed-agents/{id}/channels/{bid}/access.

    The allowlist is opt-in: an empty list means no restriction (existing
    behavior — allow all senders). Setting it populates a filter that a
    future enforcement wiring in ChannelBridge will consult.
    """

    @pytest.fixture
    def client(self, manager):
        from fastapi import FastAPI

        from openjarvis.server.agent_manager_routes import create_agent_manager_router

        app = FastAPI()
        for r in create_agent_manager_router(manager):
            app.include_router(r)
        return TestClient(app)

    @pytest.fixture
    def bound_agent(self, client):
        """Create an agent with a slack binding and yield (agent_id, binding_id)."""
        agent_id = client.post("/v1/managed-agents", json={"name": "a"}).json()["id"]
        bind_resp = client.post(
            f"/v1/managed-agents/{agent_id}/channels",
            json={"channel_type": "slack", "config": {}},
        )
        binding_id = bind_resp.json()["id"]
        return agent_id, binding_id

    def test_get_default_unrestricted(self, client, bound_agent):
        agent_id, binding_id = bound_agent
        body = client.get(
            f"/v1/managed-agents/{agent_id}/channels/{binding_id}/access"
        ).json()
        assert body["allowed_senders"] == []
        assert body["unrestricted"] is True
        assert body["binding_id"] == binding_id
        assert body["agent_id"] == agent_id
        assert body["channel_type"] == "slack"

    def test_get_404_unknown_agent(self, client):
        resp = client.get("/v1/managed-agents/nope/channels/whatever/access")
        assert resp.status_code == 404

    def test_get_404_unknown_binding(self, client, bound_agent):
        agent_id, _ = bound_agent
        resp = client.get(
            f"/v1/managed-agents/{agent_id}/channels/no-such-binding/access"
        )
        assert resp.status_code == 404

    def test_get_404_binding_belongs_to_other_agent(self, client, bound_agent):
        # Create a second agent + binding, then try to access agent A's
        # binding under agent B's URL — should 404.
        agent_a, binding_a = bound_agent
        agent_b = client.post("/v1/managed-agents", json={"name": "b"}).json()["id"]
        resp = client.get(f"/v1/managed-agents/{agent_b}/channels/{binding_a}/access")
        assert resp.status_code == 404

    def test_patch_sets_allowlist(self, client, bound_agent):
        agent_id, binding_id = bound_agent
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/channels/{binding_id}/access",
            json={"allowed_senders": ["U123ABC", "U456DEF"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["allowed_senders"] == ["U123ABC", "U456DEF"]
        assert body["unrestricted"] is False

    def test_patch_empty_list_resets_to_unrestricted(self, client, bound_agent):
        agent_id, binding_id = bound_agent
        client.patch(
            f"/v1/managed-agents/{agent_id}/channels/{binding_id}/access",
            json={"allowed_senders": ["U123"]},
        )
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/channels/{binding_id}/access",
            json={"allowed_senders": []},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["allowed_senders"] == []
        assert body["unrestricted"] is True

    def test_patch_dedupes_repeated_entries(self, client, bound_agent):
        agent_id, binding_id = bound_agent
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/channels/{binding_id}/access",
            json={"allowed_senders": ["U1", "U1", "U2"]},
        )
        assert resp.status_code == 200
        # Order preserved, duplicates removed.
        assert resp.json()["allowed_senders"] == ["U1", "U2"]

    def test_patch_strips_whitespace(self, client, bound_agent):
        agent_id, binding_id = bound_agent
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/channels/{binding_id}/access",
            json={"allowed_senders": ["  U1  ", "U2"]},
        )
        assert resp.status_code == 200
        assert resp.json()["allowed_senders"] == ["U1", "U2"]

    def test_patch_empty_string_returns_422(self, client, bound_agent):
        agent_id, binding_id = bound_agent
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/channels/{binding_id}/access",
            json={"allowed_senders": ["U1", "  ", "U2"]},
        )
        assert resp.status_code == 422
        # Original allowlist not mutated.
        body = client.get(
            f"/v1/managed-agents/{agent_id}/channels/{binding_id}/access"
        ).json()
        assert body["allowed_senders"] == []

    def test_patch_404_unknown_binding(self, client, bound_agent):
        agent_id, _ = bound_agent
        resp = client.patch(
            f"/v1/managed-agents/{agent_id}/channels/nope/access",
            json={"allowed_senders": ["U1"]},
        )
        assert resp.status_code == 404

    def test_patch_persists_across_get(self, client, bound_agent):
        agent_id, binding_id = bound_agent
        client.patch(
            f"/v1/managed-agents/{agent_id}/channels/{binding_id}/access",
            json={"allowed_senders": ["U1", "U2"]},
        )
        body = client.get(
            f"/v1/managed-agents/{agent_id}/channels/{binding_id}/access"
        ).json()
        assert body["allowed_senders"] == ["U1", "U2"]
        assert body["unrestricted"] is False


def test_run_agent_concurrent_returns_409(tmp_path):
    """Rapid Run Now clicks should not spawn multiple ticks."""
    from openjarvis.agents.manager import AgentManager

    mgr = AgentManager(db_path=str(tmp_path / "test.db"))
    agent = mgr.create_agent("Test", config={"schedule_type": "manual"})
    aid = agent["id"]

    # Simulate first click acquiring the tick
    mgr.start_tick(aid)

    # Second click should fail
    with pytest.raises(ValueError, match="already executing a tick"):
        mgr.start_tick(aid)

    mgr.end_tick(aid)


@pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")
class TestAgentManagerStreaming:
    """Tests for the SSE streaming mode of the managed-agent messages endpoint.

    The new implementation uses engine.stream_full() for real token streaming
    instead of agent.run() + word-by-word replay.
    """

    @pytest.fixture
    def _mock_engine(self):
        """Create a mock engine with a working stream_full() method."""
        from openjarvis.engine._stubs import StreamChunk

        engine = MagicMock()
        engine.engine_id = "mock"
        engine._model = "test-model"
        engine.health.return_value = True

        # Default stream_full: echo the last user message token-by-token
        async def _stream_full(messages, *, model, **kwargs):
            # Find the last user message content
            last_content = ""
            for m in reversed(messages):
                if hasattr(m, "role") and m.role.value == "user":
                    last_content = m.content
                    break
            response = f"Echo: {last_content}"
            for token in response.split(" "):
                yield StreamChunk(content=token + " ")
            yield StreamChunk(finish_reason="stop")

        engine.stream_full = _stream_full
        return engine

    @pytest.fixture
    def stream_client(self, manager, _mock_engine):
        from fastapi import FastAPI

        from openjarvis.server.agent_manager_routes import create_agent_manager_router

        app = FastAPI()
        app.state.engine = _mock_engine
        app.state.bus = None

        routers = create_agent_manager_router(manager)
        # create_agent_manager_router returns 5 routers since sendblue was
        # added; the fixture unpacks all of them defensively.
        for r in routers:
            app.include_router(r)
        return TestClient(app)

    def test_send_message_stream(self, manager, stream_client):
        """Test streaming mode returns SSE response with [DONE] sentinel."""
        agent = manager.create_agent(
            name="streamer",
            agent_type="simple",
        )
        resp = stream_client.post(
            f"/v1/managed-agents/{agent['id']}/messages",
            json={"content": "What is 2+2?", "stream": True},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        # Parse SSE events
        lines = resp.text.strip().split("\n")
        data_lines = [ln for ln in lines if ln.startswith("data:")]
        assert len(data_lines) > 0
        # Last data line must be [DONE]
        assert data_lines[-1].strip() == "data: [DONE]"

    def test_send_message_stream_real_tokens(self, manager, stream_client):
        """Content arrives as real tokens, not word-burst after completion."""
        agent = manager.create_agent(
            name="streamer_tokens",
            agent_type="simple",
        )
        resp = stream_client.post(
            f"/v1/managed-agents/{agent['id']}/messages",
            json={"content": "Hello world", "stream": True},
        )
        assert resp.status_code == 200

        # Collect content tokens from stream
        content_chunks = []
        for line in resp.text.strip().split("\n"):
            if line.startswith("data:") and "[DONE]" not in line:
                raw = line[5:].strip()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                choices = data.get("choices", [{}])
                delta_content = choices[0].get("delta", {}).get("content")
                if delta_content:
                    content_chunks.append(delta_content)

        # Should have multiple token chunks (real streaming, not single burst)
        assert len(content_chunks) > 1
        full_content = "".join(content_chunks)
        assert "Echo:" in full_content
        assert "Hello world" in full_content

    def test_send_message_stream_stores_response(self, manager, stream_client):
        """After streaming, agent response is persisted in the DB."""
        agent = manager.create_agent(
            name="streamer3",
            agent_type="simple",
        )
        resp = stream_client.post(
            f"/v1/managed-agents/{agent['id']}/messages",
            json={"content": "persist me", "stream": True},
        )
        assert resp.status_code == 200

        # Check messages in DB
        messages = manager.list_messages(agent["id"])
        # Should have both the user message and the agent response
        assert len(messages) == 2
        directions = {m["direction"] for m in messages}
        assert "user_to_agent" in directions
        assert "agent_to_user" in directions
        agent_msg = next(m for m in messages if m["direction"] == "agent_to_user")
        assert "persist me" in agent_msg["content"]

    def test_send_message_stream_finish_reason(self, manager, stream_client):
        """The final chunk before [DONE] has finish_reason='stop'."""
        agent = manager.create_agent(
            name="streamer4",
            agent_type="simple",
        )
        resp = stream_client.post(
            f"/v1/managed-agents/{agent['id']}/messages",
            json={"content": "check finish", "stream": True},
        )
        # Collect all data chunks (excluding [DONE])
        chunks = []
        for line in resp.text.strip().split("\n"):
            if line.startswith("data:") and "[DONE]" not in line:
                raw = line[5:].strip()
                try:
                    chunks.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue

        # Last chunk should have finish_reason="stop"
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"

    def test_send_message_stream_error_handling(self, manager):
        """Engine errors are reported gracefully via SSE."""

        error_engine = MagicMock()
        error_engine.engine_id = "error"
        error_engine._model = "test-model"

        async def _stream_full_error(messages, *, model, **kwargs):
            raise RuntimeError("LLM connection failed")
            yield  # make it a generator  # noqa: E501

        error_engine.stream_full = _stream_full_error

        from fastapi import FastAPI
        from fastapi.testclient import TestClient as TC

        from openjarvis.server.agent_manager_routes import create_agent_manager_router

        app = FastAPI()
        app.state.engine = error_engine
        app.state.bus = None
        routers = create_agent_manager_router(manager)
        for r in routers:
            app.include_router(r)
        client = TC(app)

        agent = manager.create_agent(name="err_agent", agent_type="simple")
        resp = client.post(
            f"/v1/managed-agents/{agent['id']}/messages",
            json={"content": "fail", "stream": True},
        )
        assert resp.status_code == 200
        assert "Error:" in resp.text or "error" in resp.text.lower()
        assert "data: [DONE]" in resp.text


@pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")
class TestResolveToolSpecs:
    """Unit tests for _resolve_tool_specs — converts template string
    tool names into OpenAI-format function specs so the engine can
    actually bind them to the model.
    """

    @pytest.fixture
    def _registered_tools(self):
        """Re-register tools after the autouse conftest fixture clears them."""
        import importlib
        import sys

        from openjarvis.core.registry import ToolRegistry

        for mod_name in list(sys.modules):
            if (
                mod_name.startswith("openjarvis.tools.")
                and not mod_name.endswith("_stubs")
                and not mod_name.endswith("agent_tools")
            ):
                try:
                    importlib.reload(sys.modules[mod_name])
                except Exception:
                    pass
        yield ToolRegistry

    def test_string_names_resolve_to_openai_specs(self, _registered_tools):
        from openjarvis.server.agent_manager_routes import _resolve_tool_specs

        specs = _resolve_tool_specs(["file_read", "think"])
        assert len(specs) == 2
        names = [s["function"]["name"] for s in specs]
        assert "file_read" in names
        assert "think" in names
        for s in specs:
            assert s["type"] == "function"
            assert "description" in s["function"]
            assert "parameters" in s["function"]

    def test_unknown_names_dropped(self, _registered_tools):
        from openjarvis.server.agent_manager_routes import _resolve_tool_specs

        specs = _resolve_tool_specs(["file_read", "nonexistent_tool_xyz"])
        assert len(specs) == 1
        assert specs[0]["function"]["name"] == "file_read"

    def test_dict_entries_passed_through(self, _registered_tools):
        from openjarvis.server.agent_manager_routes import _resolve_tool_specs

        full_spec = {
            "type": "function",
            "function": {
                "name": "custom",
                "description": "x",
                "parameters": {"type": "object"},
            },
        }
        specs = _resolve_tool_specs([full_spec, "file_read"])
        assert len(specs) == 2
        assert specs[0] is full_spec

    def test_empty_and_none_return_empty_list(self):
        from openjarvis.server.agent_manager_routes import _resolve_tool_specs

        assert _resolve_tool_specs(None) == []
        assert _resolve_tool_specs([]) == []
