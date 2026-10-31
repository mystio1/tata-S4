/** Application frame: sidebar, top metric strip and the active page. */

import type { ReactNode } from 'react';

import type { PageId } from '@/store/useQoSenseStore';
import { useQoSenseStore } from '@/store/useQoSenseStore';
import { CLASS_COLOR, congestionColor } from '@/lib/colors';
import { NOT_MEASURED, elapsedLabel, humanise, num } from '@/lib/format';
import { Badge, DataModeBadge, StatTile } from './primitives';

interface NavEntry {
  id: PageId;
  label: string;
  glyph: string;
}

const NAV: NavEntry[] = [
  { id: 'overview', label: 'Network Overview', glyph: '◉' },
  { id: 'traffic', label: 'Live Traffic', glyph: '≋' },
  { id: 'intelligence', label: 'AI Intelligence', glyph: '◈' },
  { id: 'controller', label: 'QoS Controller', glyph: '⚙' },
  { id: 'experiments', label: 'Experiments', glyph: '▶' },
  { id: 'performance', label: 'Performance', glyph: '◳' },
  { id: 'safety', label: 'Safety & Logs', glyph: '⚠' },
  { id: 'settings', label: 'Settings', glyph: '⋯' },
];

function Sidebar() {
  const page = useQoSenseStore((s) => s.page);
  const setPage = useQoSenseStore((s) => s.setPage);
  const system = useQoSenseStore((s) => s.system);
  const connection = useQoSenseStore((s) => s.connection);

  return (
    <nav className="sidebar" aria-label="Main">
      <div className="sidebar__brand">
        <span className="sidebar__mark" aria-hidden="true">
          Qo
        </span>
        <span>
          <span className="sidebar__title">QoSense</span>
          <br />
          <span className="sidebar__subtitle">Adaptive intent-aware QoS</span>
        </span>
      </div>

      <div className="sidebar__nav">
        {NAV.map((entry) => (
          <button
            key={entry.id}
            type="button"
            className="sidebar__link"
            aria-current={page === entry.id ? 'page' : undefined}
            onClick={() => setPage(entry.id)}
          >
            <span className="sidebar__link-icon" aria-hidden="true">
              {entry.glyph}
            </span>
            {entry.label}
          </button>
        ))}
      </div>

      <div className="sidebar__footer">
        <div>Build {system?.build_version ?? '—'}</div>
        <div>
          Event stream:{' '}
          <span
            style={{
              color:
                connection === 'open'
                  ? 'var(--success)'
                  : connection === 'connecting'
                    ? 'var(--warning)'
                    : 'var(--text-faint)',
            }}
          >
            {connection}
          </span>
        </div>
        <div style={{ marginTop: 6 }}>
          Quality of Service · Round-trip time · Light Gradient Boosting Machine
        </div>
      </div>
    </nav>
  );
}

function TopMetricStrip() {
  const snapshot = useQoSenseStore((s) => s.snapshot);
  const experiment = useQoSenseStore((s) => s.experiment);
  const capacityQuality = snapshot?.capacity_quality ?? 'NO_DATA';

  const congestion = snapshot?.congestion_state ?? 'UNKNOWN';
  const mode = experiment?.config.mode;
  const modeAvailability = experiment?.mode_availability;

  return (
    <div className="metric-strip">
      <StatTile
        label="Download"
        value={num(snapshot?.download_mbps, 1)}
        unit="Mbps"
        accent={CLASS_COLOR.INTERACTIVE}
        note="Measured delivered rate"
      />
      <StatTile
        label="Upload"
        value={num(snapshot?.upload_mbps, 1)}
        unit="Mbps"
        accent={CLASS_COLOR.GAMING_LIKE}
        note="Measured delivered rate"
      />
      <StatTile
        label="Usable capacity"
        value={num(snapshot?.usable_capacity_mbps, 1)}
        unit="Mbps"
        accent="var(--bronze)"
        note={
          <>
            Estimated ·{' '}
            <span
              style={{
                color:
                  capacityQuality === 'GOOD'
                    ? 'var(--success)'
                    : capacityQuality === 'NO_DATA'
                      ? 'var(--text-faint)'
                      : 'var(--warning)',
              }}
            >
              {humanise(capacityQuality)}
            </span>
          </>
        }
      />
      <StatTile
        label="Measured congestion"
        value={humanise(congestion)}
        accent={congestionColor(congestion)}
        note={
          snapshot?.added_delay_ms !== undefined && snapshot?.added_delay_ms !== null
            ? `+${num(snapshot.added_delay_ms, 1)} ms over baseline`
            : 'No delay measurement'
        }
      />
      <StatTile
        label="Predicted risk (Model 2)"
        value={snapshot?.congestion_prediction === 'UNAVAILABLE'
          ? 'Unavailable'
          : humanise(snapshot?.congestion_prediction ?? 'UNAVAILABLE')}
        accent="var(--text-faint)"
        note="Separate from measured congestion"
      />
      <StatTile
        label="Data mode"
        value={snapshot?.data_mode ?? experiment?.config.data_mode ?? 'SIMULATION'}
        accent="var(--bronze)"
        note={
          experiment
            ? `${elapsedLabel(experiment.elapsed_seconds)} of ${elapsedLabel(
                experiment.config.duration_seconds,
              )}`
            : 'No experiment created'
        }
      />
      <StatTile
        label="Mode"
        value={mode ? humanise(mode) : NOT_MEASURED}
        accent={modeAvailability?.degraded ? 'var(--warning)' : 'var(--champagne)'}
        note={
          modeAvailability?.degraded ? (
            <span style={{ color: 'var(--warning)' }}>Degraded: models unavailable</span>
          ) : (
            experiment?.config.scenario_name ?? 'No scenario selected'
          )
        }
      />
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const experiment = useQoSenseStore((s) => s.experiment);
  const connection = useQoSenseStore((s) => s.connection);
  const dropped = useQoSenseStore((s) => s.droppedEvents);
  const dataMode = experiment?.config.data_mode ?? 'SIMULATION';

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="app-main">
        <header
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 'var(--gap)',
            padding: '8px var(--gap-md)',
            borderBottom: '1px solid var(--border)',
            background: 'var(--surface)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--gap-sm)' }}>
            <DataModeBadge mode={dataMode} />
            {experiment && (
              <span className="small faint mono">
                {experiment.config.experiment_id} · seed {experiment.config.seed}
              </span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--gap-sm)' }}>
            {dropped > 0 && (
              <Badge tone="warning" title="Events were dropped for this client.">
                {dropped} events dropped
              </Badge>
            )}
            {connection !== 'open' && (
              <Badge tone={connection === 'connecting' ? 'warning' : 'critical'} dot>
                Stream {connection}
              </Badge>
            )}
            {experiment && (
              <Badge
                tone={
                  experiment.status === 'RUNNING'
                    ? 'success'
                    : experiment.status === 'FAILED'
                      ? 'critical'
                      : 'muted'
                }
                dot
              >
                {humanise(experiment.status)}
              </Badge>
            )}
          </div>
        </header>
        <TopMetricStrip />
        <main className="app-content">{children}</main>
      </div>
    </div>
  );
}
