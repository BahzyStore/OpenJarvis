"""Tests for WebSocket event bridge."""

from __future__ import annotations

import time

import pytest

from openjarvis.core.events import EventBus, EventType

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def app(event_bus):
    from openjarvis.server.ws_bridge import create_ws_router

    app = FastAPI()
    router = create_ws_router(event_bus)
    app.include_router(router)
    return app


class TestWSBridge:
    def test_websocket_receives_events(self, app, event_bus):
        client = TestClient(app)
        with client.websocket_connect("/v1/agents/events") as ws:
            event_bus.publish(
                EventType.AGENT_TICK_START,
                {
                    "agent_id": "test-123",
                    "agent_name": "test",
                },
            )
            time.sleep(0.05)  # Let call_soon_threadsafe deliver to queue
            data = ws.receive_json()
            assert data["type"] == "agent_tick_start"
            assert data["data"]["agent_id"] == "test-123"

    def test_websocket_filters_by_agent_id(self, app, event_bus):
        client = TestClient(app)
        with client.websocket_connect("/v1/agents/events?agent_id=agent-A") as ws:
            # This event should NOT be received (different agent)
            event_bus.publish(EventType.AGENT_TICK_START, {"agent_id": "agent-B"})
            # This event SHOULD be received
            event_bus.publish(EventType.AGENT_TICK_START, {"agent_id": "agent-A"})
            time.sleep(0.05)  # Let call_soon_threadsafe deliver to queue
            data = ws.receive_json()
            assert data["data"]["agent_id"] == "agent-A"

    def test_data_source_refused_forwarded(self, app, event_bus):
        """obs-3: AG-9 refusal events reach unfiltered WebSocket clients."""
        client = TestClient(app)
        with client.websocket_connect("/v1/agents/events") as ws:
            event_bus.publish(
                EventType.DATA_SOURCE_REFUSED,
                {
                    "source_id": "gmail",
                    "allowed_data_sources": ["slack"],
                    "tool": "knowledge_search",
                },
            )
            time.sleep(0.05)
            data = ws.receive_json()
            assert data["type"] == "data_source_refused"
            assert data["data"]["source_id"] == "gmail"
            assert data["data"]["tool"] == "knowledge_search"
            assert data["data"]["allowed_data_sources"] == ["slack"]

    def test_channel_message_refused_forwarded(self, app, event_bus):
        """obs-3: MC-4 sender refusal events reach unfiltered WebSocket clients."""
        client = TestClient(app)
        with client.websocket_connect("/v1/agents/events") as ws:
            event_bus.publish(
                EventType.CHANNEL_MESSAGE_REFUSED,
                {
                    "sender_id": "user_evil",
                    "channel_type": "slack",
                    "reason": "no_binding_allowlist_match",
                },
            )
            time.sleep(0.05)
            data = ws.receive_json()
            assert data["type"] == "channel_message_refused"
            assert data["data"]["sender_id"] == "user_evil"
            assert data["data"]["channel_type"] == "slack"
            assert data["data"]["reason"] == "no_binding_allowlist_match"
