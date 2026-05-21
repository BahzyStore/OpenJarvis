import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { ArrowLeft } from 'lucide-react';
import { HudPanel } from '../components/Jarvis/HudPanel';
import { VoiceCircle, type VoicePhase } from '../components/Jarvis/VoiceCircle';
import {
  fetchManagedAgents,
  fetchTelemetry,
  getBase,
  type ManagedAgent,
} from '../lib/api';
import { listConnectors } from '../lib/connectors-api';
import { useAppStore } from '../lib/store';
import type { ConnectorInfo } from '../types/connectors';
import {
  fetchVoiceStatus,
  playAudioBlob,
  synthesizeSpeech,
  transcribeAudio,
  type VoiceStatus,
} from '../lib/voice-api';

// ---------------------------------------------------------------------------
// JARVIS — full-screen ambient HUD
// ---------------------------------------------------------------------------

const HUD_VERSION = 'v0.10.0';
const WEATHER_CITY = 'MUMBAI';

interface TelemetryStats {
  cpu_percent?: number;
  memory_percent?: number;
  agents_running?: number;
  active_streams?: number;
  uptime_seconds?: number;
  // unknown fields tolerated
  [k: string]: unknown;
}

interface CalendarEvent {
  title: string;
  start: string;
}

interface NoteEntry {
  title: string;
}

// ---------------------------------------------------------------------------
// Mini hooks
// ---------------------------------------------------------------------------

function useClock(): { time: string; date: string } {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const time = now.toLocaleTimeString('en-GB', { hour12: false });
  const date = now
    .toLocaleDateString('en-US', {
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: '2-digit',
    })
    .toUpperCase();
  return { time, date };
}

function useTelemetry(): { stats: TelemetryStats | null; offline: boolean } {
  const [stats, setStats] = useState<TelemetryStats | null>(null);
  const [offline, setOffline] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const data = (await fetchTelemetry()) as TelemetryStats;
        if (cancelled) return;
        setStats(data || {});
        setOffline(false);
      } catch {
        if (!cancelled) setOffline(true);
      }
    };
    refresh();
    const id = setInterval(refresh, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);
  return { stats, offline };
}

function useAgentsList(): { agents: ManagedAgent[]; loaded: boolean } {
  const [agents, setAgents] = useState<ManagedAgent[]>([]);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const list = await fetchManagedAgents();
        if (cancelled) return;
        setAgents(list);
        setLoaded(true);
      } catch {
        if (!cancelled) setLoaded(true);
      }
    };
    refresh();
    const id = setInterval(refresh, 10000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);
  return { agents, loaded };
}

function useConnectors(): { connectors: ConnectorInfo[]; loaded: boolean } {
  const [connectors, setConnectors] = useState<ConnectorInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const list = await listConnectors();
        if (cancelled) return;
        setConnectors(list);
        setLoaded(true);
      } catch {
        if (!cancelled) setLoaded(true);
      }
    };
    refresh();
    const id = setInterval(refresh, 30000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);
  return { connectors, loaded };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function JarvisHud() {
  const navigate = useNavigate();
  const { time, date } = useClock();
  const { stats, offline: statsOffline } = useTelemetry();
  const { agents, loaded: agentsLoaded } = useAgentsList();
  const { connectors, loaded: connectorsLoaded } = useConnectors();
  const selectedModel = useAppStore((s) => s.selectedModel);
  const serverInfo = useAppStore((s) => s.serverInfo);

  // -- Voice ------------------------------------------------------------------
  const [voice, setVoice] = useState<VoiceStatus>({ transcribe: false, speak: false });
  const [phase, setPhase] = useState<VoicePhase>('idle');
  const [transcript, setTranscript] = useState('');
  const [reply, setReply] = useState('');
  const [typedReply, setTypedReply] = useState('');
  const [voiceError, setVoiceError] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const recordingRef = useRef<boolean>(false);
  const spaceHeldRef = useRef<boolean>(false);

  // Probe voice availability once.
  useEffect(() => {
    fetchVoiceStatus()
      .then(setVoice)
      .catch(() => setVoice({ transcribe: false, speak: false }));
  }, []);

  // Typewriter for Jarvis reply.
  useEffect(() => {
    if (!reply) {
      setTypedReply('');
      return;
    }
    setTypedReply('');
    let i = 0;
    const id = setInterval(() => {
      i += 1;
      setTypedReply(reply.slice(0, i));
      if (i >= reply.length) clearInterval(id);
    }, 18);
    return () => clearInterval(id);
  }, [reply]);

  const runningAgents = useMemo(
    () => agents.filter((a) => a.status === 'running'),
    [agents],
  );
  const gcalConnector = useMemo(
    () => connectors.find((c) => c.connector_id === 'gcalendar'),
    [connectors],
  );
  const obsidianConnector = useMemo(
    () => connectors.find((c) => c.connector_id === 'obsidian'),
    [connectors],
  );

  // Calendar + recent notes (placeholders unless a real API exists).
  const upcomingEvents: CalendarEvent[] = useMemo(() => {
    // No frontend calendar API yet; show empty list and rely on connector flag.
    return [];
  }, []);
  const recentNotes: NoteEntry[] = useMemo(() => {
    // Without a knowledge-recent endpoint, leave empty and rely on chunk count.
    return [];
  }, []);

  // -- Recording flow ---------------------------------------------------------

  const startRecording = useCallback(async () => {
    if (recordingRef.current) return;
    if (!voice.transcribe) {
      setVoiceError('Voice transcription offline');
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setVoiceError('Microphone not supported');
      return;
    }
    setVoiceError(null);
    setTranscript('');
    setReply('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      audioChunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      recordingRef.current = true;
      setPhase('listening');
    } catch {
      setVoiceError('Microphone access denied');
      setPhase('idle');
    }
  }, [voice.transcribe]);

  const askJarvis = useCallback(async (prompt: string): Promise<string> => {
    setPhase('thinking');
    const base = getBase();
    const model = selectedModel || serverInfo?.model || '';
    try {
      const res = await fetch(`${base}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model,
          messages: [
            { role: 'system', content: 'You are JARVIS, an onboard AI assistant. Reply concisely, in one or two sentences.' },
            { role: 'user', content: prompt },
          ],
          stream: false,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: { choices?: Array<{ message?: { content?: string } }> } = await res.json();
      const text = data.choices?.[0]?.message?.content?.trim() || '';
      return text || '(no response)';
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'request failed';
      return `(jarvis unavailable: ${msg})`;
    }
  }, [selectedModel, serverInfo?.model]);

  const stopRecording = useCallback(async () => {
    if (!recordingRef.current) return;
    recordingRef.current = false;
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state !== 'recording') {
      setPhase('idle');
      return;
    }
    setPhase('transcribing');
    await new Promise<void>((resolve) => {
      recorder.onstop = () => resolve();
      recorder.stop();
    });
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    const blob = new Blob(audioChunksRef.current, {
      type: recorder.mimeType || 'audio/webm',
    });
    audioChunksRef.current = [];
    try {
      const result = await transcribeAudio(blob);
      const text = result.text?.trim() || '';
      if (!text) {
        setPhase('idle');
        return;
      }
      setTranscript(text);
      const answer = await askJarvis(text);
      setReply(answer);
      if (voice.speak) {
        setPhase('speaking');
        try {
          const audio = await synthesizeSpeech(answer);
          if (audio) await playAudioBlob(audio);
        } catch {
          /* ignore playback failure */
        }
      }
      setPhase('idle');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'transcription failed';
      setVoiceError(msg);
      setPhase('idle');
    }
  }, [askJarvis, voice.speak]);

  // Spacebar push-to-talk (don't hijack while typing into something).
  useEffect(() => {
    const isTypingTarget = (t: EventTarget | null) => {
      if (!(t instanceof HTMLElement)) return false;
      const tag = t.tagName;
      return tag === 'INPUT' || tag === 'TEXTAREA' || t.isContentEditable;
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || e.repeat) return;
      if (isTypingTarget(e.target)) return;
      e.preventDefault();
      if (spaceHeldRef.current) return;
      spaceHeldRef.current = true;
      void startRecording();
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code !== 'Space') return;
      if (!spaceHeldRef.current) return;
      spaceHeldRef.current = false;
      e.preventDefault();
      void stopRecording();
    };
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
    };
  }, [startRecording, stopRecording]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
      const r = mediaRecorderRef.current;
      if (r && r.state === 'recording') r.stop();
    };
  }, []);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const cpu = numberOrDash(stats?.cpu_percent);
  const mem = numberOrDash(stats?.memory_percent);
  const agentsRunningCount =
    typeof stats?.agents_running === 'number'
      ? stats!.agents_running
      : runningAgents.length;

  const voiceDisabled = !voice.transcribe;

  return (
    <div className="jarvis-hud-root">
      {/* Back button */}
      <button
        type="button"
        onClick={() => navigate('/')}
        className="jarvis-hud-back"
        aria-label="Exit HUD"
      >
        <ArrowLeft size={16} />
        <span>EXIT</span>
      </button>

      {/* Top-left: branding */}
      <HudPanel
        label="J.A.R.V.I.S"
        className="jarvis-pos-top-left"
        live
      >
        <div className="jarvis-wordmark jarvis-mono">JARVIS</div>
        <div className="jarvis-mono jarvis-text-xs jarvis-accent">ONLINE</div>
        <div className="jarvis-mono jarvis-text-xs jarvis-dim">{HUD_VERSION}</div>
      </HudPanel>

      {/* Top-center: clock */}
      <div className="jarvis-pos-clock">
        <div className="jarvis-clock-time jarvis-mono">{time}</div>
        <div className="jarvis-clock-date jarvis-mono">{date}</div>
      </div>

      {/* Top-right: system status */}
      <HudPanel
        label="SYS // TELEMETRY"
        className="jarvis-pos-top-right"
        live={!statsOffline}
      >
        {statsOffline ? (
          <div className="jarvis-mono jarvis-text-xs jarvis-dim">STAT FEED OFFLINE</div>
        ) : (
          <>
            <Row k="CPU" v={`${cpu}%`} />
            <Row k="MEM" v={`${mem}%`} />
            <Row k="AGENTS" v={`${agentsRunningCount} RUN`} />
            <Row k="STREAMS" v={`${stats?.active_streams ?? 0}`} />
          </>
        )}
      </HudPanel>

      {/* Mid-left: weather */}
      <HudPanel label="WEATHER" className="jarvis-pos-mid-left">
        <div className="jarvis-mono jarvis-text-lg">{WEATHER_CITY}</div>
        <div className="jarvis-mono jarvis-text-md jarvis-accent">29°C</div>
        <div className="jarvis-mono jarvis-text-xs jarvis-dim">CLEAR · HUMIDITY 62%</div>
      </HudPanel>

      {/* Mid-right: calendar */}
      <HudPanel label="CALENDAR" className="jarvis-pos-mid-right">
        {!connectorsLoaded ? (
          <div className="jarvis-mono jarvis-text-xs jarvis-dim">SYNCING…</div>
        ) : !gcalConnector?.connected ? (
          <div className="jarvis-mono jarvis-text-xs jarvis-dim">CONNECT CALENDAR</div>
        ) : upcomingEvents.length === 0 ? (
          <div className="jarvis-mono jarvis-text-xs jarvis-dim">NO UPCOMING EVENTS</div>
        ) : (
          upcomingEvents.slice(0, 4).map((ev, i) => (
            <div key={i} className="jarvis-mono jarvis-text-xs">
              <span className="jarvis-accent">{ev.start}</span> {ev.title}
            </div>
          ))
        )}
      </HudPanel>

      {/* Bottom-left: obsidian notes */}
      <HudPanel label="KNOWLEDGE // OBSIDIAN" className="jarvis-pos-bot-left">
        {!connectorsLoaded ? (
          <div className="jarvis-mono jarvis-text-xs jarvis-dim">SYNCING…</div>
        ) : !obsidianConnector?.connected ? (
          <div className="jarvis-mono jarvis-text-xs jarvis-dim">VAULT NOT LINKED</div>
        ) : recentNotes.length === 0 ? (
          <div className="jarvis-mono jarvis-text-xs jarvis-dim">
            {(obsidianConnector.chunks ?? 0).toLocaleString()} CHUNKS INDEXED
          </div>
        ) : (
          recentNotes.slice(0, 5).map((n, i) => (
            <div key={i} className="jarvis-mono jarvis-text-xs jarvis-trunc">
              › {n.title}
            </div>
          ))
        )}
      </HudPanel>

      {/* Bottom-right: agents */}
      <HudPanel label="ACTIVE AGENTS" className="jarvis-pos-bot-right">
        {!agentsLoaded ? (
          <div className="jarvis-mono jarvis-text-xs jarvis-dim">SCANNING…</div>
        ) : runningAgents.length === 0 ? (
          <div className="jarvis-mono jarvis-text-xs jarvis-dim">
            {agents.length === 0 ? 'NO AGENTS DEPLOYED' : 'ALL AGENTS IDLE'}
          </div>
        ) : (
          <>
            <div className="jarvis-mono jarvis-text-lg jarvis-accent">
              {runningAgents.length}
              <span className="jarvis-text-xs jarvis-dim"> RUNNING</span>
            </div>
            {runningAgents.slice(0, 4).map((a) => (
              <div key={a.id} className="jarvis-mono jarvis-text-xs jarvis-trunc">
                › {a.name}
              </div>
            ))}
          </>
        )}
      </HudPanel>

      {/* Voice transcript / reply overlay */}
      {(transcript || reply || voiceError) && (
        <div className="jarvis-overlay">
          {voiceError && (
            <div className="jarvis-mono jarvis-text-xs" style={{ color: 'var(--color-error)' }}>
              ERR · {voiceError}
            </div>
          )}
          {transcript && (
            <div className="jarvis-mono jarvis-text-sm">
              <span className="jarvis-dim">YOU ›</span> {transcript}
            </div>
          )}
          {typedReply && (
            <div className="jarvis-mono jarvis-text-sm">
              <span className="jarvis-accent">JARVIS ›</span> {typedReply}
              {typedReply.length < reply.length && <span className="hud-caret" />}
            </div>
          )}
        </div>
      )}

      {/* Bottom-center: voice circle */}
      <div className="jarvis-pos-voice">
        <VoiceCircle
          phase={phase}
          disabled={voiceDisabled}
          onPointerDown={() => void startRecording()}
          onPointerUp={() => void stopRecording()}
        />
        {voiceDisabled && (
          <div className="jarvis-mono jarvis-text-xs jarvis-dim" style={{ marginTop: 8 }}>
            VOICE BACKEND OFFLINE · {voice.reason || 'transcribe unavailable'}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function numberOrDash(n: unknown): string {
  if (typeof n !== 'number' || Number.isNaN(n)) return '—';
  return n.toFixed(0);
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="jarvis-row">
      <span className="jarvis-mono jarvis-text-xs jarvis-dim">{k}</span>
      <span className="jarvis-mono jarvis-text-xs">{v}</span>
    </div>
  );
}
