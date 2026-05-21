"""Tests for the /v1/voice API router (STT + TTS HTTP endpoints).

All heavyweight STT/TTS engines are mocked — no real Whisper / Kokoro
models are loaded.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.server import voice_router as voice_router_mod  # noqa: E402
from openjarvis.server.voice_router import create_voice_router  # noqa: E402
from openjarvis.speech._stubs import TranscriptionResult  # noqa: E402
from openjarvis.speech.tts import TTSResult  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_instance_caches():
    """Reset the per-process engine instance caches between tests."""
    voice_router_mod._stt_instances.clear()
    voice_router_mod._tts_instances.clear()
    yield
    voice_router_mod._stt_instances.clear()
    voice_router_mod._tts_instances.clear()


@pytest.fixture
def app() -> TestClient:
    _app = FastAPI()
    _app.include_router(create_voice_router())
    return TestClient(_app)


def _make_mock_stt(text: str = "hello world") -> MagicMock:
    backend = MagicMock()
    backend.transcribe.return_value = TranscriptionResult(
        text=text,
        language="en",
        confidence=0.95,
        duration_seconds=1.234,
        segments=[],
    )
    backend.health.return_value = True
    return backend


def _make_mock_tts(audio: bytes = b"FAKEWAV", fmt: str = "wav") -> MagicMock:
    backend = MagicMock()
    backend.synthesize.return_value = TTSResult(
        audio=audio,
        format=fmt,
        voice_id="default",
        sample_rate=24000,
        duration_seconds=0.5,
    )
    backend.health.return_value = True
    return backend


# ---------------------------------------------------------------------------
# POST /v1/voice/transcribe
# ---------------------------------------------------------------------------


def test_transcribe_happy_path(app, monkeypatch):
    """A valid audio upload yields a transcription dict."""
    mock_stt = _make_mock_stt("hi there")

    def _fake_get_stt(engine_id: str) -> Any:
        assert engine_id == "faster_whisper"
        return mock_stt

    monkeypatch.setattr(voice_router_mod, "_get_stt", _fake_get_stt)

    resp = app.post(
        "/v1/voice/transcribe",
        files={"audio": ("clip.wav", b"\x00\x01\x02", "audio/wav")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["text"] == "hi there"
    assert data["language"] == "en"
    assert data["engine"] == "faster_whisper"
    # 1.234s -> 1234 ms
    assert data["duration_ms"] == 1234

    # Ensure the mock was actually called with the audio bytes + wav format.
    assert mock_stt.transcribe.called
    call_args = mock_stt.transcribe.call_args
    assert call_args.args[0] == b"\x00\x01\x02"
    assert call_args.kwargs.get("format") == "wav"


def test_transcribe_missing_audio_returns_422(app):
    """No 'audio' field -> 422."""
    resp = app.post("/v1/voice/transcribe", data={"engine": "faster_whisper"})
    assert resp.status_code == 422


def test_transcribe_unknown_engine_returns_422(app):
    """Unknown engine id -> 422 (and STT is never touched)."""
    resp = app.post(
        "/v1/voice/transcribe",
        data={"engine": "totally_made_up"},
        files={"audio": ("clip.wav", b"abc", "audio/wav")},
    )
    assert resp.status_code == 422
    assert "totally_made_up" in resp.json()["detail"]


def test_transcribe_engine_failure_returns_503(app, monkeypatch):
    """If the STT engine raises, the endpoint returns 503."""
    failing = MagicMock()
    failing.transcribe.side_effect = RuntimeError("model not loaded")

    monkeypatch.setattr(voice_router_mod, "_get_stt", lambda _id: failing)

    resp = app.post(
        "/v1/voice/transcribe",
        files={"audio": ("clip.wav", b"abc", "audio/wav")},
    )
    assert resp.status_code == 503
    assert "model not loaded" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /v1/voice/speak
# ---------------------------------------------------------------------------


def test_speak_happy_path(app, monkeypatch):
    """A valid text payload yields audio bytes with audio/wav content-type."""
    mock_tts = _make_mock_tts(b"FAKEWAV", fmt="wav")

    def _fake_get_tts(engine_id: str) -> Any:
        assert engine_id == "kokoro_tts"
        return mock_tts

    monkeypatch.setattr(voice_router_mod, "_get_tts", _fake_get_tts)

    resp = app.post(
        "/v1/voice/speak",
        json={"text": "hello", "engine": "kokoro_tts", "voice": "default"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("audio/wav")
    assert resp.content == b"FAKEWAV"
    assert mock_tts.synthesize.called


def test_speak_missing_text_returns_422(app):
    """Missing the 'text' field -> 422 (pydantic validation)."""
    resp = app.post("/v1/voice/speak", json={"engine": "kokoro_tts"})
    assert resp.status_code == 422


def test_speak_empty_text_returns_422(app):
    """An empty 'text' value -> 422."""
    resp = app.post(
        "/v1/voice/speak",
        json={"text": "   ", "engine": "kokoro_tts"},
    )
    assert resp.status_code == 422


def test_speak_unknown_engine_returns_422(app):
    """Unknown TTS engine id -> 422."""
    resp = app.post(
        "/v1/voice/speak",
        json={"text": "hi", "engine": "bogus_engine"},
    )
    assert resp.status_code == 422


def test_speak_engine_failure_returns_503(app, monkeypatch):
    """If the TTS engine raises, the endpoint returns 503."""
    failing = MagicMock()
    failing.synthesize.side_effect = RuntimeError("KOKORO not installed")

    monkeypatch.setattr(voice_router_mod, "_get_tts", lambda _id: failing)

    resp = app.post(
        "/v1/voice/speak",
        json={"text": "hi", "engine": "kokoro_tts"},
    )
    assert resp.status_code == 503
    assert "KOKORO" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /v1/voice/status
# ---------------------------------------------------------------------------


def test_status_lists_engines(app):
    """Status returns at least one STT and one TTS engine, each with the
    expected shape."""
    resp = app.get("/v1/voice/status")
    assert resp.status_code == 200
    data: Dict[str, Any] = resp.json()

    assert "stt_engines" in data
    assert "tts_engines" in data
    assert len(data["stt_engines"]) >= 1
    assert len(data["tts_engines"]) >= 1

    for entry in data["stt_engines"] + data["tts_engines"]:
        assert "id" in entry
        assert "display_name" in entry
        assert "available" in entry
        assert "reason" in entry
        assert isinstance(entry["available"], bool)

    stt_ids = {e["id"] for e in data["stt_engines"]}
    tts_ids = {e["id"] for e in data["tts_engines"]}
    # The public ids declared in the router must show up.
    assert "faster_whisper" in stt_ids
    assert "kokoro_tts" in tts_ids


def test_status_reports_missing_api_key(app, monkeypatch):
    """A cloud engine with no API key reports available=False with a
    helpful reason."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)

    resp = app.get("/v1/voice/status")
    assert resp.status_code == 200
    data = resp.json()

    by_id = {e["id"]: e for e in data["stt_engines"] + data["tts_engines"]}

    deepgram = by_id.get("deepgram")
    assert deepgram is not None
    assert deepgram["available"] is False
    # The reason is either the missing-API-key message (if the deepgram
    # SDK is installed) or the missing-module message (if not). Both are
    # valid "not ready" states.
    assert (
        "DEEPGRAM_API_KEY" in deepgram["reason"]
        or "not importable" in deepgram["reason"]
    )
