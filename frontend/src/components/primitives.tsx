/** Small presentational building blocks shared by every page. */

import type { ReactNode } from 'react';

import type { DataMode, SafetyCheck } from '@/api/types';
import { NOT_MEASURED } from '@/lib/format';
import { modeBadgeClass } from '@/lib/colors';

export type Tone = 'muted' | 'success' | 'warning' | 'critical';

export function Badge({
  children,
  tone = 'muted',
  dot = false,
  title,
}: {
  children: ReactNode;
  tone?: Tone;
  dot?: boolean;
  title?: string;
}) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      {dot && <span className="badge__dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

/**
 * The data-mode badge. It appears on every page because the interface must
 * never let simulated values be mistaken for measurements from a real link.
 */
export function DataModeBadge({ mode, title }: { mode: DataMode; title?: string }) {
  const explain =
    mode === 'SIMULATION'
      ? 'Values come from the deterministic laboratory simulator, not from a real network.'
      : mode === 'REPLAY'
        ? 'Values are replayed from a recorded experiment.'
        : 'Values are measured by the connected Ubuntu laboratory agent.';
  return (
    <span className={modeBadgeClass(mode)} title={title ?? explain}>
      <span className="badge__dot" aria-hidden="true" />
      {mode}
    </span>
  );
}

export function Panel({
  title,
  step,
  hint,
  actions,
  children,
  flush = false,
  style,
}: {
  title: string;
  step?: number;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <section className={`panel${flush ? ' panel--flush' : ''}`} style={style}>
      <header className="panel__head">
        <h2 className="panel__title">
          {step !== undefined && (
            <span className="panel__step" aria-hidden="true">
              {step}
            </span>
          )}
          {title}
        </h2>
        {actions ?? (hint ? <span className="panel__hint">{hint}</span> : null)}
      </header>
      <div className="panel__body">{children}</div>
    </section>
  );
}

export function StatTile({
  label,
  value,
  unit,
  note,
  accent,
  icon,
}: {
  label: string;
  value: string;
  unit?: string;
  note?: ReactNode;
  accent?: string;
  icon?: ReactNode;
}) {
  const unavailable = value === NOT_MEASURED;
  return (
    <div className="stat-tile">
      <span className="stat-tile__label">
        {icon}
        {label}
      </span>
      <span
        className="stat-tile__value"
        style={{ color: unavailable ? 'var(--text-faint)' : (accent ?? 'var(--champagne)') }}
      >
        {value}
        {unit && !unavailable && <span className="stat-tile__unit">{unit}</span>}
      </span>
      {note && <span className="stat-tile__note">{note}</span>}
    </div>
  );
}

export function KeyValue({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([key, value]) => (
        <div key={key} style={{ display: 'contents' }}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <div className="bar" role="presentation">
      <div className="bar__fill" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="empty-state">
      <span>{title}</span>
      {hint && <span className="small">{hint}</span>}
    </div>
  );
}

export function Notice({
  children,
  tone = 'info',
}: {
  children: ReactNode;
  tone?: 'info' | 'warning' | 'critical' | 'success';
}) {
  const className = tone === 'info' ? 'notice' : `notice notice--${tone}`;
  return <div className={className}>{children}</div>;
}

export function CheckList({ checks }: { checks: SafetyCheck[] }) {
  if (checks.length === 0) {
    return <EmptyState title="No checks have run yet." />;
  }
  return (
    <ul className="check-list">
      {checks.map((check) => (
        <li className="check" key={check.name}>
          <span
            className="check__mark"
            style={{ color: check.passed ? 'var(--success)' : 'var(--critical)' }}
            aria-hidden="true"
          >
            {check.passed ? '✓' : '✕'}
          </span>
          <span>
            <span className="check__name">{check.name.replace(/_/g, ' ')}</span>
            <br />
            <span className="check__detail">{check.detail}</span>
          </span>
          <span className="sr-only">{check.passed ? 'passed' : 'failed'}</span>
        </li>
      ))}
    </ul>
  );
}

export function PathChain({ steps }: { steps: string[] }) {
  if (steps.length === 0) return <span className="faint small">No path reported.</span>;
  return (
    <div className="path-chain">
      {steps.map((step, index) => (
        <span key={step} style={{ display: 'contents' }}>
          {index > 0 && (
            <span className="path-chain__arrow" aria-hidden="true">
              →
            </span>
          )}
          <span className="path-chain__step">{step}</span>
        </span>
      ))}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions}
    </div>
  );
}
