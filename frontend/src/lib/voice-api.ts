// ---------------------------------------------------------------------------
// Voice API wrappers — used by the Jarvis HUD.
//
// These talk to the OpenJarvis speech endpoints (`/v1/speech/*`) and a future
// `/v1/voice/speak` synthesis endpoint. Each function degrades gracefully if
// the underlying endpoint is missing (404) so the HUD remains usable on
// older backends.
// ---------------------------------------------------------------------------

import { getBase, isTauri, transcribeAudio as apiTranscribeAudio, fetchSpeechHealth } from './api';
import type { TranscriptionResult, SpeechHealth } from './api';

export type { TranscriptionResult, SpeechHealth };

export interface VoiceStatus {
  transcribe: boolean;
  speak: boolean;
  backend?: string;
  reason?: string;
}

/**
 * Send an audio blob to the transcription endpoint.
 * Delegates to the existing api.ts implementation which already handles the
 * Tauri/web fallback.
 */
export async function transcribeAudio(blob: Blob, filename = 'recording.webm'): Promise<TranscriptionResult> {
  return apiTranscribeAudio(blob, filename);
}

/**
 * Attempt to synthesize speech for `text` and return playable audio.
 *
 * Tries `/v1/speech/synthesize` first, then `/v1/voice/speak`. Returns a Blob
 * the caller can pipe into an <audio> element. Returns `null` if no backend
 * endpoint is available so the caller can fall back to text-only display.
 */
export async function synthesizeSpeech(text: string): Promise<Blob | null> {
  const base = getBase();
  const candidates: Array<{ url: string; body: unknown }> = [
    { url: `${base}/v1/speech/synthesize`, body: { text } },
    { url: `${base}/v1/voice/speak`, body: { text } },
  ];
  for (const c of candidates) {
    try {
      const res = await fetch(c.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(c.body),
      });
      if (!res.ok) continue;
      const contentType = res.headers.get('content-type') || '';
      if (contentType.includes('application/json')) {
        // Some backends return { audio: <base64> } or { url: <url> }
        const data = await res.json().catch(() => null);
        if (data?.audio) {
          const bin = atob(data.audio);
          const bytes = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
          return new Blob([bytes], { type: data.mime || 'audio/mpeg' });
        }
        if (typeof data?.url === 'string') {
          const r = await fetch(data.url);
          if (r.ok) return await r.blob();
        }
        continue;
      }
      return await res.blob();
    } catch {
      // try next candidate
    }
  }
  return null;
}

/**
 * Probe which voice endpoints are available. Used by the HUD to render the
 * "Voice offline" tag instead of crashing when nothing is wired up yet.
 */
export async function fetchVoiceStatus(): Promise<VoiceStatus> {
  // Transcription health comes from the existing /v1/speech/health endpoint.
  let transcribe = false;
  let backend: string | undefined;
  let reason: string | undefined;
  try {
    const h: SpeechHealth = await fetchSpeechHealth();
    transcribe = !!h.available;
    backend = h.backend;
    reason = h.reason;
  } catch {
    transcribe = false;
  }

  // Synthesis is best-effort — we issue a HEAD to /v1/voice/status and accept
  // any non-5xx response as "available". If both probes fail, we report no
  // speak support; the HUD will then skip TTS playback.
  let speak = false;
  if (!isTauri()) {
    const base = getBase();
    for (const path of ['/v1/speech/voices', '/v1/voice/status']) {
      try {
        const res = await fetch(`${base}${path}`);
        if (res.ok) { speak = true; break; }
      } catch {
        /* try next */
      }
    }
  }
  return { transcribe, speak, backend, reason };
}

/**
 * Play a synthesized audio blob via a transient <audio> element.
 * Returns a promise that resolves when playback ends (or rejects on error).
 */
export async function playAudioBlob(blob: Blob): Promise<void> {
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  try {
    await audio.play();
    await new Promise<void>((resolve, reject) => {
      audio.onended = () => resolve();
      audio.onerror = () => reject(new Error('Audio playback error'));
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}
