import { useEffect, useState, useCallback } from 'react';
import { Shield, ShieldCheck, ShieldAlert, Loader2 } from 'lucide-react';
import {
  fetchAgentDataSourceAccess,
  updateAgentDataSourceAccess,
} from '../lib/api';
import type { AgentDataSourceAccess } from '../lib/api';

// ---------------------------------------------------------------------------
// AG-9: Per-agent data-source access panel
//
// Backend semantics:
//   []         -> default-deny (no sources permitted)
//   ["*"]      -> wildcard (all permitted)
//   ["a","b"]  -> explicit grants
// ---------------------------------------------------------------------------

interface Props {
  agentId: string;
}

export function AgentDataSourceAccessPanel({ agentId }: Props) {
  const [state, setState] = useState<AgentDataSourceAccess | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await fetchAgentDataSourceAccess(agentId);
      setState(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to load access';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  async function commit(next: string[]) {
    setSaving(true);
    setError(null);
    try {
      const data = await updateAgentDataSourceAccess(agentId, next);
      setState(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to save access';
      setError(msg);
    } finally {
      setSaving(false);
    }
  }

  function toggleSource(source: string, checked: boolean) {
    if (!state) return;
    const current = state.wildcard ? [] : state.allowed_data_sources;
    const next = checked
      ? Array.from(new Set([...current, source]))
      : current.filter((s) => s !== source);
    void commit(next);
  }

  function grantWildcard() {
    void commit(['*']);
  }

  function restrictToSpecific() {
    // Switch from wildcard to default-deny so user can opt-in selectively.
    void commit([]);
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const containerStyle: React.CSSProperties = {
    background: 'var(--color-bg-secondary)',
    border: '1px solid var(--color-border)',
  };

  if (loading) {
    return (
      <div className="p-3 rounded-lg" style={containerStyle}>
        <div className="flex items-center gap-2 mb-2">
          <Shield size={14} style={{ color: 'var(--color-text-secondary)' }} />
          <h3 className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
            Data Source Access
          </h3>
        </div>
        <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
          <Loader2 size={14} className="animate-spin" />
          Loading access policy…
        </div>
      </div>
    );
  }

  if (!state) {
    return (
      <div className="p-3 rounded-lg" style={containerStyle}>
        <div className="flex items-center gap-2 mb-2">
          <Shield size={14} style={{ color: 'var(--color-text-secondary)' }} />
          <h3 className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
            Data Source Access
          </h3>
        </div>
        {error && (
          <div className="text-sm" style={{ color: 'var(--color-error)' }}>
            {error}
          </div>
        )}
        <button
          onClick={() => { setLoading(true); void load(); }}
          className="mt-2 text-xs px-2 py-0.5 rounded cursor-pointer"
          style={{ color: 'var(--color-accent)', border: '1px solid var(--color-accent)', opacity: 0.8 }}
        >
          Retry
        </button>
      </div>
    );
  }

  const { wildcard, allowed_data_sources, available_data_sources } = state;
  const allowedSet = new Set(allowed_data_sources);
  const isDefaultDeny = !wildcard && allowed_data_sources.length === 0;

  // Header icon depends on state.
  const HeaderIcon = wildcard ? ShieldAlert : isDefaultDeny ? ShieldAlert : ShieldCheck;
  const headerIconColor = wildcard
    ? 'var(--color-warning)'
    : isDefaultDeny
      ? 'var(--color-text-tertiary)'
      : 'var(--color-success)';

  return (
    <div className="p-3 rounded-lg" style={containerStyle}>
      <div className="flex items-center gap-2 mb-2">
        <HeaderIcon size={14} style={{ color: headerIconColor }} />
        <h3 className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
          Data Source Access
        </h3>
        {saving && (
          <span className="text-xs flex items-center gap-1" style={{ color: 'var(--color-text-tertiary)' }}>
            <Loader2 size={11} className="animate-spin" />
            Saving…
          </span>
        )}
      </div>

      {/* Wildcard banner */}
      {wildcard && (
        <div
          className="flex items-center justify-between gap-3 p-2.5 rounded-md mb-2"
          style={{
            background: 'var(--color-warning-subtle, var(--color-bg))',
            border: '1px solid var(--color-warning)',
          }}
        >
          <div className="text-sm" style={{ color: 'var(--color-text)' }}>
            All data sources permitted (wildcard)
          </div>
          <button
            onClick={restrictToSpecific}
            disabled={saving}
            className="text-xs px-2 py-0.5 rounded cursor-pointer whitespace-nowrap"
            style={{
              color: 'var(--color-accent)',
              border: '1px solid var(--color-accent)',
              opacity: saving ? 0.5 : 0.9,
            }}
          >
            Restrict to specific sources
          </button>
        </div>
      )}

      {/* Default-deny notice */}
      {isDefaultDeny && (
        <div
          className="p-2.5 rounded-md mb-2 text-sm"
          style={{
            background: 'var(--color-bg)',
            border: '1px solid var(--color-border)',
            color: 'var(--color-text-secondary)',
          }}
        >
          No data sources permitted (default-deny). Check sources below to grant access.
        </div>
      )}

      {/* Source grid (always visible — checked items reflect current grants).
          When wildcard is active, every box is rendered as checked but
          unchecking one transitions the policy out of wildcard mode and
          grants only the still-checked entries. */}
      {available_data_sources.length === 0 ? (
        <div className="text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
          No registered data-source connectors available.
        </div>
      ) : (
        <div
          className="grid gap-x-4 gap-y-1.5"
          style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))' }}
        >
          {available_data_sources.map((source) => {
            const checked = wildcard || allowedSet.has(source);
            return (
              <label
                key={source}
                className="flex items-center gap-2 text-sm cursor-pointer select-none"
                style={{ color: 'var(--color-text)' }}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={saving}
                  onChange={(e) => {
                    if (wildcard && !e.target.checked) {
                      // Transition wildcard -> explicit list minus this source.
                      const next = available_data_sources.filter((s) => s !== source);
                      void commit(next);
                    } else {
                      toggleSource(source, e.target.checked);
                    }
                  }}
                  style={{ accentColor: 'var(--color-accent)' }}
                />
                <span style={{ color: checked ? 'var(--color-text)' : 'var(--color-text-secondary)' }}>
                  {source}
                </span>
              </label>
            );
          })}
        </div>
      )}

      {/* Action row: grant-all (only when not already wildcard) */}
      {!wildcard && (
        <div className="mt-3 pt-2 flex items-center gap-2" style={{ borderTop: '1px solid var(--color-border)' }}>
          <button
            onClick={grantWildcard}
            disabled={saving}
            className="text-xs px-2 py-0.5 rounded cursor-pointer"
            style={{
              color: 'var(--color-accent)',
              border: '1px solid var(--color-accent)',
              opacity: saving ? 0.5 : 0.8,
            }}
          >
            Grant all (wildcard)
          </button>
          <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
            {allowed_data_sources.length === 0
              ? 'Currently denying all data sources'
              : `${allowed_data_sources.length} of ${available_data_sources.length} source${available_data_sources.length === 1 ? '' : 's'} permitted`}
          </span>
        </div>
      )}

      {/* Inline error (e.g., 422 from PATCH) */}
      {error && (
        <div
          className="mt-2 text-sm"
          style={{ color: 'var(--color-error)' }}
          role="alert"
        >
          {error}
        </div>
      )}
    </div>
  );
}

export default AgentDataSourceAccessPanel;
