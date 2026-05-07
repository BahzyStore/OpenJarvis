import { useState, useEffect, useCallback, useMemo } from 'react';
import type { ReactNode } from 'react';
import { ShieldAlert, ChevronDown, ChevronRight } from 'lucide-react';
import { useAgentEvents, type AgentEvent } from '../../lib/useAgentEvents';

const REFUSAL_EVENT_TYPES = [
  'data_source_refused',
  'channel_message_refused',
] as const;

const MAX_EVENTS = 50;

interface DataSourceRefusedData {
  source_id?: string | null;
  dropped_sources?: string[];
  allowed_data_sources?: string[];
  tool?: string;
  agent_id?: string;
  reason?: string;
}

interface BindingContext {
  binding_id?: string;
  agent_id?: string;
  channel_type?: string;
}

interface ChannelMessageRefusedData {
  sender_id?: string;
  channel_type?: string;
  reason?: string;
  refused_by_bindings?: BindingContext[];
}

export interface RefusalEvent extends AgentEvent {
  type: 'data_source_refused' | 'channel_message_refused';
}

/**
 * Subscribe to refusal events on the global agent-event feed.
 * Returns the most recent ~50 refusal events, newest first.
 */
export function useRefusalEvents(): RefusalEvent[] {
  const [events, setEvents] = useState<RefusalEvent[]>([]);

  const handleEvent = useCallback((event: AgentEvent) => {
    if (
      event.type !== 'data_source_refused' &&
      event.type !== 'channel_message_refused'
    ) {
      return;
    }
    setEvents((prev) => {
      const next = [event as RefusalEvent, ...prev];
      if (next.length > MAX_EVENTS) next.length = MAX_EVENTS;
      return next;
    });
  }, []);

  // agentId=null subscribes to the global feed (all agents).
  useAgentEvents(null, handleEvent, REFUSAL_EVENT_TYPES);

  return events;
}

function formatRelativeTime(tsSeconds: number, nowMs: number): string {
  const diff = Math.max(0, nowMs - tsSeconds * 1000);
  const sec = Math.floor(diff / 1000);
  if (sec < 5) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

function shortAgentId(id: string | undefined): string {
  if (!id) return 'unknown';
  return id.length > 10 ? id.slice(0, 8) + '…' : id;
}

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

function DataSourceRefusedRow({ event, now }: { event: RefusalEvent; now: number }) {
  const data = event.data as DataSourceRefusedData;
  const tool = data.tool ?? 'tool';
  const agent = shortAgentId(data.agent_id);
  const allowed =
    data.allowed_data_sources && data.allowed_data_sources.length > 0
      ? data.allowed_data_sources.join(', ')
      : 'none';

  let summary: string;
  if (data.source_id) {
    summary = `[${tool}] denied agent ${agent} from ${data.source_id} (allowed: ${allowed})`;
  } else if (data.dropped_sources && data.dropped_sources.length > 0) {
    summary = `[${tool}] dropped ${data.dropped_sources.join(', ')} for ${agent}`;
  } else {
    summary = `[${tool}] refused for ${agent}`;
  }

  return (
    <RefusalRow
      kind="data"
      timestamp={event.timestamp}
      now={now}
      summary={summary}
      reason={data.reason}
      details={null}
    />
  );
}

function ChannelMessageRefusedRow({ event, now }: { event: RefusalEvent; now: number }) {
  const data = event.data as ChannelMessageRefusedData;
  const channel = data.channel_type ?? 'channel';
  const sender = data.sender_id ?? 'unknown';
  const bindings = data.refused_by_bindings ?? [];
  const summary = `${channel} blocked message from ${sender} (refused by ${bindings.length} binding${bindings.length === 1 ? '' : 's'})`;

  const details: ReactNode = bindings.length > 0 ? (
    <div className="flex flex-col gap-1">
      {bindings.map((b, i) => (
        <div
          key={i}
          className="font-mono text-[11px] flex flex-wrap gap-x-3 gap-y-0.5"
          style={{ color: 'var(--color-text-secondary)' }}
        >
          <span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>binding:</span>{' '}
            {b.binding_id ?? '—'}
          </span>
          <span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>agent:</span>{' '}
            {shortAgentId(b.agent_id)}
          </span>
          <span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>channel:</span>{' '}
            {b.channel_type ?? '—'}
          </span>
        </div>
      ))}
    </div>
  ) : null;

  return (
    <RefusalRow
      kind="channel"
      timestamp={event.timestamp}
      now={now}
      summary={summary}
      reason={data.reason}
      details={details}
    />
  );
}

function RefusalRow({
  kind,
  timestamp,
  now,
  summary,
  reason,
  details,
}: {
  kind: 'data' | 'channel';
  timestamp: number;
  now: number;
  summary: string;
  reason: string | undefined;
  details: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const expandable = details != null;
  const tint = 'var(--color-accent-amber)';

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{
        border: '1px solid var(--color-border)',
        background: `color-mix(in srgb, ${tint} 6%, transparent)`,
      }}
    >
      <button
        type="button"
        onClick={() => {
          if (expandable) setExpanded((v) => !v);
        }}
        className="flex items-start gap-2 w-full px-3 py-2 text-left text-sm"
        style={{
          cursor: expandable ? 'pointer' : 'default',
        }}
        title={reason ? `reason: ${reason}` : undefined}
      >
        {expandable ? (
          expanded ? (
            <ChevronDown size={14} style={{ color: 'var(--color-text-tertiary)', marginTop: 2 }} />
          ) : (
            <ChevronRight size={14} style={{ color: 'var(--color-text-tertiary)', marginTop: 2 }} />
          )
        ) : (
          <span style={{ width: 14, display: 'inline-block', marginTop: 2 }} />
        )}
        <span style={{ marginTop: 1 }} aria-hidden>
          🚫
        </span>
        <span className="flex-1 break-words" style={{ color: 'var(--color-text)' }}>
          {summary}
          {reason && (
            <span
              className="ml-2 text-[11px] font-mono"
              style={{ color: 'var(--color-text-tertiary)' }}
            >
              {reason}
            </span>
          )}
          <span className="ml-2 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
            · {kind === 'data' ? 'data source' : 'channel'}
          </span>
        </span>
        <span
          className="text-[11px] font-mono shrink-0"
          style={{ color: 'var(--color-text-tertiary)' }}
        >
          {formatRelativeTime(timestamp, now)}
        </span>
      </button>
      {expandable && expanded && (
        <div
          className="px-3 py-2"
          style={{
            borderTop: '1px solid var(--color-border)',
            background: 'var(--color-bg-secondary)',
          }}
        >
          {details}
        </div>
      )}
    </div>
  );
}

export function RefusalFeed() {
  const events = useRefusalEvents();
  const now = useNow(10_000);
  const tintColor = 'var(--color-accent-amber)';

  const items = useMemo(
    () =>
      events.map((ev, i) => {
        const key = `${ev.timestamp}-${i}`;
        if (ev.type === 'data_source_refused') {
          return <DataSourceRefusedRow key={key} event={ev} now={now} />;
        }
        return <ChannelMessageRefusedRow key={key} event={ev} now={now} />;
      }),
    [events, now],
  );

  return (
    <div className="hud-panel p-6">
      <h3 className="hud-label flex items-center gap-2 mb-4">
        <ShieldAlert size={12} style={{ color: tintColor }} />
        Refusal Feed
        {events.length > 0 && (
          <span
            className="ml-1 px-1.5 py-0.5 rounded-full text-[10px] font-mono"
            style={{
              background: `color-mix(in srgb, ${tintColor} 15%, transparent)`,
              color: tintColor,
            }}
          >
            {events.length}
          </span>
        )}
      </h3>
      {events.length === 0 ? (
        <div
          className="h-32 flex items-center justify-center text-sm"
          style={{ color: 'var(--color-text-tertiary)' }}
        >
          No refusals recorded yet.
        </div>
      ) : (
        <div className="flex flex-col gap-1.5 max-h-80 overflow-y-auto">{items}</div>
      )}
    </div>
  );
}
