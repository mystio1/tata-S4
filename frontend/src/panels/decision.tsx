/**
 * Panels for the decision half of the pipeline: the Priority Decision Centre,
 * the Safety Guard, enforcement and verification.
 *
 * Everything shown here is a value the backend computed. Where the policy
 * engine did not produce a number, the panel says so rather than filling the
 * space with a plausible one, and where no policy exists the rate table reads
 * "Not configured".
 */

import { useState } from 'react';

import type {
  CandidateCost,
  EnforcementResult,
  IntentLease,
  PolicyProposal,
  TrafficClass,
  VerificationResult,
} from '@/api/types';
import type { PolicyEvaluationInputs } from '@/store/useQoSenseStore';
import {
  Badge,
  Bar,
  CheckList,
  EmptyState,
  KeyValue,
  Notice,
  Panel,
  PathChain,
} from '@/components/primitives';
import { CLASS_COLOR, CLASS_LABEL } from '@/lib/colors';
import { humanise, num } from '@/lib/format';

const COST_LABEL: Record<string, string> = {
  interactive_delay_risk: 'interactive delay',
  bulk_progress_risk: 'bulk progress',
  fairness_penalty: 'fairness',
  churn_penalty: 'policy churn',
};

function CandidateRow({
  candidate,
  selected,
}: {
  candidate: CandidateCost;
  selected: boolean;
}) {
  const rejected = candidate.hard_constraint_violations.length > 0;
  const parts: [string, number | null][] = [
    ['interactive_delay_risk', candidate.interactive_delay_risk],
    ['bulk_progress_risk', candidate.bulk_progress_risk],
    ['fairness_penalty', candidate.fairness_penalty],
    ['churn_penalty', candidate.churn_penalty],
  ];

  return (
    <div
      className={`candidate${selected ? ' candidate--selected' : ''}${
        rejected ? ' candidate--rejected' : ''
      }`}
    >
      <span className="candidate__name">{humanise(candidate.profile)}</span>
      <span className="candidate__cost">
        {candidate.total_cost === null ? (
          <span className="faint">no score</span>
        ) : (
          candidate.total_cost.toFixed(3)
        )}
      </span>
      <span className="candidate__breakdown">
        {parts.map(([key, value]) => (
          <span key={key}>
            {COST_LABEL[key]} {value === null ? '—' : value.toFixed(2)}
          </span>
        ))}
      </span>
      {rejected && (
        <span className="candidate__breakdown" style={{ color: 'var(--critical)' }}>
          {candidate.hard_constraint_violations.map((v) => humanise(v)).join(' · ')}
        </span>
      )}
    </div>
  );
}

export function PriorityDecisionCentre({
  inputs,
  proposal,
}: {
  inputs: PolicyEvaluationInputs | null;
  proposal: PolicyProposal | null;
}) {
  return (
    <Panel
      title="Priority decision centre"
      step={4}
      actions={
        proposal ? (
          <Badge tone={proposal.status === 'ACCEPTED' ? 'success' : 'critical'} dot>
            {humanise(proposal.status)}
          </Badge>
        ) : (
          <Badge tone="muted">Idle</Badge>
        )
      }
    >
      {!inputs && !proposal ? (
        <EmptyState
          title="The policy engine has not evaluated yet."
          hint="In the no-QoS reference mode the controller observes and never shapes, so no evaluation runs."
        />
      ) : (
        <>
          {inputs && (
            <>
              <div className="small faint">Inputs considered</div>
              <KeyValue
                rows={[
                  [
                    'Class demand',
                    <span className="small">
                      {Object.entries(inputs.class_demand_mbps)
                        .map(([cls, mbps]) => `${CLASS_LABEL[cls as TrafficClass] ?? cls} ${mbps.toFixed(1)}`)
                        .join(' · ') || '—'}
                    </span>,
                  ],
                  [
                    'Usable capacity',
                    `${num(inputs.usable_capacity_mbps, 1)} Mbps (${humanise(
                      inputs.capacity_quality,
                    )})`,
                  ],
                  ['Added delay', `${num(inputs.added_delay_ms, 1)} ms`],
                  ['Jitter', `${num(inputs.jitter_ms, 2)} ms`],
                  ['Packet loss', `${num(inputs.packet_loss_percent, 2)} %`],
                  ['Queue occupancy', `${num(inputs.queue_occupancy_percent, 0)} %`],
                  ['Measured congestion', humanise(inputs.congestion_state)],
                  [
                    'Predicted risk',
                    humanise(inputs.congestion_prediction.risk_band),
                  ],
                  [
                    'Class evidence',
                    inputs.classification_evidence ? 'Available' : 'Insufficient',
                  ],
                  [
                    'User intent',
                    inputs.active_intents.length > 0
                      ? `${inputs.active_intents.length} active lease(s)`
                      : 'None',
                  ],
                  [
                    'Since last change',
                    `${num(inputs.seconds_since_policy_change, 1)} s`,
                  ],
                ]}
              />
            </>
          )}

          {proposal && (
            <>
              <div className="small faint" style={{ marginTop: 4 }}>
                Candidates evaluated · weights {proposal.weights_version}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {proposal.candidates.map((candidate) => (
                  <CandidateRow
                    key={candidate.profile}
                    candidate={candidate}
                    selected={candidate.profile === proposal.profile}
                  />
                ))}
              </div>

              <Notice>{proposal.explanation}</Notice>

              <div className="legend">
                {proposal.reason_codes.map((code) => (
                  <span className="legend__item" key={code}>
                    <span
                      className="legend__swatch"
                      style={{ background: 'var(--bronze)' }}
                      aria-hidden="true"
                    />
                    {humanise(code)}
                  </span>
                ))}
              </div>

              <p className="small faint" style={{ margin: 0 }}>
                A lower cost is better. Each component is computed from a measured quantity; a
                component that could not be computed is left out of the total rather than
                guessed. A hard constraint rejects a candidate whatever its score.
              </p>
            </>
          )}
        </>
      )}
    </Panel>
  );
}

export function SafetyGuardPanel({ proposal }: { proposal: PolicyProposal | null }) {
  const safety = proposal?.safety ?? null;
  return (
    <Panel
      title="Safety guard"
      step={5}
      actions={
        safety ? (
          <Badge tone={safety.accepted ? 'success' : 'critical'} dot>
            {safety.accepted ? 'Accepted' : 'Rejected'}
          </Badge>
        ) : (
          <Badge tone="muted">Not run</Badge>
        )
      }
    >
      {!safety ? (
        <EmptyState
          title="No proposal has been validated yet."
          hint="Every proposal passes through the guard before it can reach an enforcer."
        />
      ) : (
        <>
          {!safety.accepted && (
            <Notice tone="critical">
              Rejected on {safety.rejection_reasons.map((r) => humanise(r)).join(', ')}. The
              previous safe state is retained.
            </Notice>
          )}
          <CheckList checks={safety.checks} />
        </>
      )}
    </Panel>
  );
}

export function EnforcementPanel({
  enforcement,
  proposal,
  installedRates,
}: {
  enforcement: EnforcementResult | null;
  proposal: PolicyProposal | null;
  installedRates: Record<string, number>;
}) {
  const status = enforcement?.status;
  const tone =
    status === 'APPLIED'
      ? 'success'
      : status === 'SIMULATED'
        ? 'warning'
        : status === undefined
          ? 'muted'
          : 'critical';

  const rates = Object.entries(installedRates);
  const maxRate = Math.max(1, ...rates.map(([, value]) => value));

  return (
    <Panel
      title="Policy enforcement"
      step={6}
      actions={
        <Badge tone={tone} dot>
          {status ? humanise(status) : 'Nothing installed'}
        </Badge>
      }
    >
      {status === 'SIMULATED' && (
        <Notice tone="warning">
          Applied to the deterministic simulator, not to Linux traffic control. A real
          application is only reported once an Ubuntu laboratory agent has installed the named
          profile and the actual queue discipline state has been read back.
        </Notice>
      )}
      {status && status !== 'SIMULATED' && status !== 'APPLIED' && (
        <Notice tone="critical">{enforcement?.error ?? 'Enforcement did not succeed.'}</Notice>
      )}

      <div className="small faint">Bandwidth classes</div>
      {rates.length === 0 ? (
        <div className="faint small">Not configured. No class rate limit is installed.</div>
      ) : (
        <div className="rate-stack">
          {rates
            .sort((a, b) => b[1] - a[1])
            .map(([cls, value]) => (
              <div className="rate-row" key={cls}>
                <span style={{ color: CLASS_COLOR[cls as TrafficClass] ?? 'var(--text)' }}>
                  {CLASS_LABEL[cls as TrafficClass] ?? cls}
                </span>
                <Bar
                  value={value}
                  max={maxRate}
                  color={CLASS_COLOR[cls as TrafficClass] ?? 'var(--bronze)'}
                />
                <span className="mono num">{value.toFixed(1)} Mbps</span>
              </div>
            ))}
        </div>
      )}

      {proposal && proposal.class_allocations.length > 0 && (
        <p className="small faint" style={{ margin: 0 }}>
          Ceilings are derived from the usable capacity estimate and the measured per-class
          demand, not from a fixed percentage split. Guaranteed floors are the firm commitment;
          spare capacity is lent between classes.
        </p>
      )}

      {enforcement?.read_back && (
        <>
          <div className="small faint" style={{ marginTop: 4 }}>
            Read-back from {enforcement.read_back.source}
          </div>
          <div className="small faint">Upload path</div>
          <PathChain steps={enforcement.read_back.upload_path} />
          <div className="small faint">Download path</div>
          <PathChain steps={enforcement.read_back.download_path} />
          <KeyValue
            rows={[
              [
                'Matches intent',
                enforcement.read_back.matches_intent ? (
                  <span style={{ color: 'var(--success)' }}>Yes</span>
                ) : (
                  <span style={{ color: 'var(--critical)' }}>No</span>
                ),
              ],
              ['Transaction', enforcement.transaction_id],
              ['Snapshot', enforcement.snapshot_id ?? '—'],
              ['Apply time', `${enforcement.apply_duration_ms.toFixed(2)} ms`],
            ]}
          />
        </>
      )}
    </Panel>
  );
}

export function VerificationPanel({
  verification,
}: {
  verification: VerificationResult | null;
}) {
  const outcome = verification?.outcome;
  const tone =
    outcome === 'ACCEPTABLE'
      ? 'success'
      : outcome === 'DEGRADED'
        ? 'critical'
        : outcome === 'INCONCLUSIVE'
          ? 'warning'
          : 'muted';

  return (
    <Panel
      title="Verification and final decision"
      step={7}
      actions={
        <Badge tone={tone} dot>
          {outcome ? humanise(outcome) : 'Awaiting a policy change'}
        </Badge>
      }
    >
      {!verification ? (
        <EmptyState
          title="No policy has been verified yet."
          hint="A policy is measured against the interval before it was installed, using the same probe method."
        />
      ) : (
        <>
          <Notice tone={outcome === 'ACCEPTABLE' ? 'success' : 'warning'}>
            {verification.explanation}
          </Notice>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Metric</th>
                  <th scope="col" className="num">
                    Before
                  </th>
                  <th scope="col" className="num">
                    After
                  </th>
                  <th scope="col" className="num">
                    Change
                  </th>
                </tr>
              </thead>
              <tbody>
                <BeforeAfterRow
                  label="Added delay (ms)"
                  before={verification.before_added_delay_ms}
                  after={verification.after_added_delay_ms}
                  lowerIsBetter
                />
                <BeforeAfterRow
                  label="Jitter (ms)"
                  before={verification.before_jitter_ms}
                  after={verification.after_jitter_ms}
                  lowerIsBetter
                />
                <BeforeAfterRow
                  label="Packet loss (%)"
                  before={verification.before_packet_loss_percent}
                  after={verification.after_packet_loss_percent}
                  lowerIsBetter
                />
                <BeforeAfterRow
                  label="Queue occupancy (%)"
                  before={verification.before_queue_occupancy_percent}
                  after={verification.after_queue_occupancy_percent}
                  lowerIsBetter
                />
                <BeforeAfterRow
                  label="Bulk goodput (Mbps)"
                  before={verification.before_bulk_goodput_mbps}
                  after={verification.after_bulk_goodput_mbps}
                  lowerIsBetter={false}
                />
              </tbody>
            </table>
          </div>
          <CheckList checks={verification.criteria} />
          <p className="small faint" style={{ margin: 0 }}>
            Both windows use the same probe method and the same{' '}
            {verification.measurement_window_seconds} second length.
          </p>
        </>
      )}
    </Panel>
  );
}

function BeforeAfterRow({
  label,
  before,
  after,
  lowerIsBetter,
}: {
  label: string;
  before: number | null;
  after: number | null;
  lowerIsBetter: boolean;
}) {
  const measurable = before !== null && after !== null;
  const delta = measurable ? after - before : null;
  const improved = delta === null ? null : lowerIsBetter ? delta <= 0 : delta >= 0;

  return (
    <tr>
      <td>{label}</td>
      <td className="num">{num(before, 2)}</td>
      <td className="num">{num(after, 2)}</td>
      <td
        className="num"
        style={{
          color:
            improved === null
              ? 'var(--text-faint)'
              : improved
                ? 'var(--success)'
                : 'var(--critical)',
        }}
      >
        {delta === null ? '—' : `${delta >= 0 ? '+' : ''}${delta.toFixed(2)}`}
      </td>
    </tr>
  );
}

export function IntentPanel({
  intents,
  devices,
  busy,
  onCreate,
  onCancel,
}: {
  intents: IntentLease[];
  devices: { device_id: string; label: string }[];
  busy: boolean;
  onCreate: (targetId: string, durationSeconds: number) => void;
  onCancel: (intentId: string) => void;
}) {
  return (
    <Panel title="Temporary priority lease" hint="Expiry is enforced by the server">
      {intents.length === 0 ? (
        <div className="faint small">No lease is active.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--gap-sm)' }}>
          {intents.map((lease) => (
            <div
              key={lease.intent_id}
              style={{
                border: '1px solid var(--bronze-dim)',
                borderRadius: 'var(--radius-sm)',
                padding: 'var(--gap-sm)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span>{lease.target_id}</span>
                <span className="mono">{Math.max(0, Math.round(lease.remaining_seconds))} s left</span>
              </div>
              <div className="small faint">
                {humanise(lease.intent_type)} · {lease.reason || 'no reason given'}
              </div>
              <button
                type="button"
                className="btn btn--danger"
                style={{ marginTop: 6 }}
                disabled={busy}
                onClick={() => onCancel(lease.intent_id)}
              >
                Cancel lease
              </button>
            </div>
          ))}
        </div>
      )}

      <IntentForm devices={devices} busy={busy} onCreate={onCreate} />

      <p className="small faint" style={{ margin: 0 }}>
        A lease is an input to the policy engine, not an override of it. The Safety Guard can
        still reject a policy a lease asked for, and when a lease expires the ordinary policy is
        re-evaluated rather than the display quietly changing.
      </p>
    </Panel>
  );
}

function IntentForm({
  devices,
  busy,
  onCreate,
}: {
  devices: { device_id: string; label: string }[];
  busy: boolean;
  onCreate: (targetId: string, durationSeconds: number) => void;
}) {
  const [target, setTarget] = useState(devices[0]?.device_id ?? '');
  const [duration, setDuration] = useState(300);

  return (
    <div className="field-row" style={{ alignItems: 'end' }}>
      <label className="field">
        <span className="field__label">Device</span>
        <select
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          disabled={devices.length === 0}
        >
          {devices.map((device) => (
            <option key={device.device_id} value={device.device_id}>
              {device.label}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field__label">Duration (seconds)</span>
        <input
          type="number"
          min={10}
          max={3600}
          step={10}
          value={duration}
          onChange={(e) => setDuration(Number(e.target.value))}
        />
      </label>
      <button
        type="button"
        className="btn"
        disabled={busy || !target}
        onClick={() => onCreate(target, duration)}
      >
        Request priority
      </button>
    </div>
  );
}
