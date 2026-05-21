"""FastAPI router for /v1/voice — STT + TTS HTTP endpoints.

Exposes three endpoints:

* ``POST /v1/voice/transcribe`` — multipart audio upload, returns transcript.
* ``POST /v1/voice/speak``      — JSON text, returns synthesised audio bytes.
* ``GET  /v1/voice/status``     — lists available STT/TTS engines.

Engines are looked up through :mod:`openjarvis.core.registry` (the same
``SpeechRegistry`` / ``TTSRegistry`` used elsewhere). The router maps the
public, snake_case engine ids used in the API (``faster_whisper``,
``openai_whisper``, ``deepgram``, ``kokoro_tts``, ``openai_tts``,
``cartesia_tts``) onto the underlying registry keys.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Tuple

# Module-level FastAPI + pydantic imports — type annotations on route
# functions must resolve via `typing.get_type_hints()` against the
# module's globals because `from __future__ import annotations` turns
# them into strings. Wrapped in a try so the module still imports in
# environments without FastAPI.
try:
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import Response
    from pydantic import BaseModel
except ImportError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]
    Response = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment,misc]


if APIRouter is not None:

    class SpeakRequest(BaseModel):
        text: str
        engine: str = "kokoro_tts"
        voice: str = "default"


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Engine id mapping (public API id -> registry key)
# ---------------------------------------------------------------------------

# Public STT ids accepted on the wire -> registry key in ``SpeechRegistry``.
_STT_ENGINES: Dict[str, Dict[str, str]] = {
    "faster_whisper": {
        "registry_key": "faster-whisper",
        "display_name": "Faster-Whisper (local)",
        "env": "",  # no API key required
    },
    "openai_whisper": {
        "registry_key": "openai",
        "display_name": "OpenAI Whisper API",
        "env": "OPENAI_API_KEY",
    },
    "deepgram": {
        "registry_key": "deepgram",
        "display_name": "Deepgram",
        "env": "DEEPGRAM_API_KEY",
    },
}

# Public TTS ids accepted on the wire -> registry key in ``TTSRegistry``.
_TTS_ENGINES: Dict[str, Dict[str, str]] = {
    "kokoro_tts": {
        "registry_key": "kokoro",
        "display_name": "Kokoro TTS (local)",
        "env": "",  # no API key required (needs the kokoro package)
    },
    "openai_tts": {
        "registry_key": "openai_tts",
        "display_name": "OpenAI TTS",
        "env": "OPENAI_API_KEY",
    },
    "cartesia_tts": {
        "registry_key": "cartesia",
        "display_name": "Cartesia TTS",
        "env": "CARTESIA_API_KEY",
    },
}


# ---------------------------------------------------------------------------
# Engine instance cache (per-process)
# ---------------------------------------------------------------------------

_stt_instances: Dict[str, Any] = {}
_tts_instances: Dict[str, Any] = {}


def _import_speech_modules() -> None:
    """Make sure the speech backends have had a chance to register."""
    try:
        import openjarvis.speech  # noqa: F401
    except Exception as exc:
        logger.debug("openjarvis.speech import failed: %s", exc)


def _get_stt(engine_id: str) -> Any:
    """Return a cached STT backend instance for *engine_id*.

    Raises ``KeyError`` if the engine id is unknown, ``RuntimeError`` if
    the underlying backend cannot be instantiated.
    """
    if engine_id not in _STT_ENGINES:
        raise KeyError(engine_id)
    if engine_id in _stt_instances:
        return _stt_instances[engine_id]

    _import_speech_modules()
    from openjarvis.core.registry import SpeechRegistry

    registry_key = _STT_ENGINES[engine_id]["registry_key"]
    if not SpeechRegistry.contains(registry_key):
        raise RuntimeError(f"STT engine '{engine_id}' is not installed")

    cls = SpeechRegistry.get(registry_key)
    instance = cls()
    _stt_instances[engine_id] = instance
    return instance


def _get_tts(engine_id: str) -> Any:
    """Return a cached TTS backend instance for *engine_id*."""
    if engine_id not in _TTS_ENGINES:
        raise KeyError(engine_id)
    if engine_id in _tts_instances:
        return _tts_instances[engine_id]

    _import_speech_modules()
    from openjarvis.core.registry import TTSRegistry

    registry_key = _TTS_ENGINES[engine_id]["registry_key"]
    if not TTSRegistry.contains(registry_key):
        raise RuntimeError(f"TTS engine '{engine_id}' is not installed")

    cls = TTSRegistry.get(registry_key)
    instance = cls()
    _tts_instances[engine_id] = instance
    return instance


def _availability(
    engine_id: str,
    info: Dict[str, str],
    *,
    is_stt: bool,
) -> Tuple[bool, str]:
    """Return ``(available, reason)`` for a single engine."""
    _import_speech_modules()

    if is_stt:
        from openjarvis.core.registry import SpeechRegistry as _Reg
    else:
        from openjarvis.core.registry import TTSRegistry as _Reg

    if not _Reg.contains(info["registry_key"]):
        return False, "backend module not importable (package missing?)"

    env_var = info.get("env", "")
    if env_var and not os.environ.get(env_var):
        return False, f"{env_var} not set"

    try:
        get_fn = _get_stt if is_stt else _get_tts
        instance = get_fn(engine_id)
    except Exception as exc:
        return False, f"failed to instantiate: {exc}"

    health = getattr(instance, "health", None)
    if callable(health):
        try:
            if not health():
                return False, "health() returned False"
        except Exception as exc:
            return False, f"health() raised: {exc}"

    return True, "ready"


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_voice_router():
    """Return an APIRouter exposing /v1/voice/{transcribe,speak,status}.

    FastAPI / pydantic are imported lazily so that test environments
    without them do not crash at import time.
    """
    if APIRouter is None:
        raise ImportError("fastapi is required for the voice router")

    try:
        from pydantic import BaseModel  # noqa: F401  (ensure pydantic ok)
    except ImportError as exc:  # pragma: no cover
        raise ImportError("pydantic is required for the voice router") from exc

    router = APIRouter(prefix="/v1/voice", tags=["voice"])

    # ------------------------------------------------------------------
    # POST /v1/voice/transcribe
    # ------------------------------------------------------------------

    @router.post("/transcribe")
    async def transcribe(request: Request) -> Dict[str, Any]:
        """Transcribe an uploaded audio file to text."""
        form = await request.form()

        audio_file = form.get("audio")
        if audio_file is None or not hasattr(audio_file, "read"):
            raise HTTPException(
                status_code=422,
                detail="Missing 'audio' file field",
            )

        engine_id = form.get("engine") or "faster_whisper"
        if isinstance(engine_id, bytes):
            engine_id = engine_id.decode("utf-8", errors="ignore")
        engine_id = str(engine_id)

        if engine_id not in _STT_ENGINES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown STT engine '{engine_id}'. Valid: {sorted(_STT_ENGINES)}"
                ),
            )

        language = form.get("language")
        if isinstance(language, bytes):
            language = language.decode("utf-8", errors="ignore")
        language = str(language) if language else None

        audio_bytes = await audio_file.read()

        # Detect format from filename suffix.
        filename = getattr(audio_file, "filename", "") or ""
        ext = "wav"
        if "." in filename:
            ext = filename.rsplit(".", 1)[-1].lower()

        try:
            backend = _get_stt(engine_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

        try:
            result = backend.transcribe(audio_bytes, format=ext, language=language)
        except Exception as exc:
            logger.warning("STT engine '%s' failed: %s", engine_id, exc)
            raise HTTPException(status_code=503, detail=str(exc))

        duration_ms = int(round(getattr(result, "duration_seconds", 0.0) * 1000))
        return {
            "text": getattr(result, "text", ""),
            "language": getattr(result, "language", None),
            "duration_ms": duration_ms,
            "engine": engine_id,
        }

    # ------------------------------------------------------------------
    # POST /v1/voice/speak
    # ------------------------------------------------------------------

    @router.post("/speak")
    async def speak(req: SpeakRequest, request: Request):
        """Synthesise text into audio bytes."""
        if not req.text or not req.text.strip():
            raise HTTPException(status_code=422, detail="Missing 'text'")

        if req.engine not in _TTS_ENGINES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown TTS engine '{req.engine}'. Valid: {sorted(_TTS_ENGINES)}"
                ),
            )

        fmt = request.query_params.get("format", "wav").lower()

        try:
            backend = _get_tts(req.engine)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

        # Empty / "default" voice tells the backend to pick its own default.
        voice_id = req.voice if req.voice and req.voice != "default" else ""

        try:
            kwargs: Dict[str, Any] = {"output_format": fmt}
            if voice_id:
                kwargs["voice_id"] = voice_id
            result = backend.synthesize(req.text, **kwargs)
        except Exception as exc:
            logger.warning("TTS engine '%s' failed: %s", req.engine, exc)
            raise HTTPException(status_code=503, detail=str(exc))

        audio_bytes = getattr(result, "audio", result)
        out_fmt = getattr(result, "format", fmt) or fmt
        content_type = _mime_for_format(out_fmt)

        return Response(content=audio_bytes, media_type=content_type)

    # ------------------------------------------------------------------
    # GET /v1/voice/status
    # ------------------------------------------------------------------

    @router.get("/status")
    async def status() -> Dict[str, Any]:
        """List the available STT and TTS engines on this server."""
        stt: List[Dict[str, Any]] = []
        for engine_id, info in _STT_ENGINES.items():
            available, reason = _availability(engine_id, info, is_stt=True)
            stt.append(
                {
                    "id": engine_id,
                    "display_name": info["display_name"],
                    "available": available,
                    "reason": reason,
                }
            )

        tts: List[Dict[str, Any]] = []
        for engine_id, info in _TTS_ENGINES.items():
            available, reason = _availability(engine_id, info, is_stt=False)
            tts.append(
                {
                    "id": engine_id,
                    "display_name": info["display_name"],
                    "available": available,
                    "reason": reason,
                }
            )

        return {"stt_engines": stt, "tts_engines": tts}

    return router


def _mime_for_format(fmt: str) -> str:
    """Map an audio format string to a MIME type."""
    fmt = (fmt or "wav").lower().lstrip(".")
    return {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
        "webm": "audio/webm",
        "m4a": "audio/mp4",
    }.get(fmt, "application/octet-stream")


__all__ = ["create_voice_router"]
