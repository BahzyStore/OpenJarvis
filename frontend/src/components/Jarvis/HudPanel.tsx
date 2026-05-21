import type { CSSProperties, ReactNode } from 'react';

interface HudPanelProps {
  label: string;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  live?: boolean;
  /** Sets a wider/uppercase title block instead of the default label */
  titleSize?: 'sm' | 'md';
}

/**
 * Iron-Man-style HUD panel: thin cyan border, uppercase label header,
 * subtle ambient "breathe" animation, content rendered in monospace.
 *
 * Designed to be absolutely positioned by the caller (see JarvisHud).
 */
export function HudPanel({
  label,
  children,
  className,
  style,
  live,
  titleSize = 'sm',
}: HudPanelProps) {
  return (
    <div
      className={`jarvis-hud-panel ${className ?? ''}`}
      style={style}
    >
      <div className="jarvis-hud-panel-head">
        <span
          className="jarvis-hud-label"
          style={{ fontSize: titleSize === 'md' ? '0.78rem' : '0.68rem' }}
        >
          {label}
        </span>
        {live && <span className="jarvis-hud-live" aria-hidden="true" />}
      </div>
      <div className="jarvis-hud-panel-body">{children}</div>
    </div>
  );
}
