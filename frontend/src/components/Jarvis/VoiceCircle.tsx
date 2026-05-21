import type { CSSProperties } from 'react';

export type VoicePhase =
  | 'idle'
  | 'listening'
  | 'transcribing'
  | 'thinking'
  | 'speaking';

interface VoiceCircleProps {
  phase: VoicePhase;
  disabled?: boolean;
  onPointerDown: () => void;
  onPointerUp: () => void;
  size?: number;
}

const PHASE_LABEL: Record<VoicePhase, string> = {
  idle: 'PRESS & HOLD TO SPEAK',
  listening: 'LISTENING…',
  transcribing: 'TRANSCRIBING…',
  thinking: 'JARVIS THINKING…',
  speaking: 'JARVIS SPEAKING…',
};

/**
 * Central activation circle for the Jarvis HUD.
 *
 * Push-to-talk: pointerdown begins recording, pointerup stops it. The same
 * gestures are wired to spacebar at the page level.
 */
export function VoiceCircle({
  phase,
  disabled,
  onPointerDown,
  onPointerUp,
  size = 168,
}: VoiceCircleProps) {
  const active = phase === 'listening';
  const busy = phase === 'transcribing' || phase === 'thinking' || phase === 'speaking';

  const baseStyle: CSSProperties = {
    width: size,
    height: size,
  };

  return (
    <div
      className="jarvis-voice-circle-wrap"
      style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}
    >
      <button
        type="button"
        aria-label="Voice activation"
        aria-pressed={active}
        disabled={disabled}
        onPointerDown={(e) => {
          if (disabled) return;
          e.preventDefault();
          onPointerDown();
        }}
        onPointerUp={(e) => {
          if (disabled) return;
          e.preventDefault();
          onPointerUp();
        }}
        onPointerLeave={(e) => {
          if (!active) return;
          e.preventDefault();
          onPointerUp();
        }}
        className={`jarvis-voice-circle ${active ? 'is-listening' : ''} ${busy ? 'is-busy' : ''}`}
        style={baseStyle}
      >
        <span className="jarvis-voice-circle-ring" aria-hidden="true" />
        <span className="jarvis-voice-circle-ring jarvis-voice-circle-ring-2" aria-hidden="true" />
        <span className="jarvis-voice-circle-core" aria-hidden="true" />
        <span className="jarvis-voice-circle-icon" aria-hidden="true">
          {/* simple mic glyph in pure SVG so we don't pull in icons */}
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none">
            <path
              d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"
              stroke="currentColor"
              strokeWidth="1.6"
            />
            <path
              d="M5 11a7 7 0 0 0 14 0M12 18v4"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </svg>
        </span>
      </button>
      <div
        className="jarvis-voice-circle-label"
        aria-live="polite"
      >
        {disabled ? 'VOICE OFFLINE' : PHASE_LABEL[phase]}
      </div>
    </div>
  );
}
