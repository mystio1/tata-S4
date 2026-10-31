/**
 * Frontend tests.
 *
 * The theme running through these: the interface must never turn an
 * unmeasured value into a confident-looking one, and the scene must describe
 * what the backend reported rather than inventing motion of its own.
 */

import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { FlowTable } from '@/components/FlowTable';
import { EventTimeline } from '@/components/EventTimeline';
import { Topology2D } from '@/scene/Topology2D';
import {
  MAX_PARTICLES_PER_FLOW,
  PACKETS_PER_PARTICLE,
  particlesForPacketRate,
} from '@/scene/PacketStream';
import {
  CongestionPredictorPanel,
  TrafficClassifierPanel,
} from '@/panels/observation';
import { EnforcementPanel, PriorityDecisionCentre } from '@/panels/decision';
import { NOT_MEASURED, bytes, humanise, integer, num, seconds } from '@/lib/format';
import { useQoSenseStore } from '@/store/useQoSenseStore';
import * as fx from './fixtures';

// ---------------------------------------------------------------- formatting

describe('formatting never invents a value', () => {
  it('renders an em dash for a value that was not measured', () => {
    expect(num(null)).toBe(NOT_MEASURED);
    expect(num(undefined)).toBe(NOT_MEASURED);
    expect(num(Number.NaN)).toBe(NOT_MEASURED);
    expect(integer(null)).toBe(NOT_MEASURED);
    expect(bytes(null)).toBe(NOT_MEASURED);
    expect(seconds(null)).toBe(NOT_MEASURED);
  });

  it('does not confuse a measured zero with a missing value', () => {
    expect(num(0)).toBe('0.0');
    expect(integer(0)).toBe('0');
  });

  it('formats numbers to the requested precision', () => {
    expect(num(42.4567, 2)).toBe('42.46');
    expect(integer(1234)).toBe('1,234');
  });

  it('turns enum names into readable words', () => {
    expect(humanise('PROTECT_INTERACTIVE')).toBe('Protect interactive');
  });
});

// ------------------------------------------------------------ particle rate

describe('packet stream aggregation', () => {
  it('draws nothing for a flow sending nothing', () => {
    expect(particlesForPacketRate(0)).toBe(0);
  });

  it('scales particle count with the measured packet rate', () => {
    const low = particlesForPacketRate(PACKETS_PER_PARTICLE * 2);
    const high = particlesForPacketRate(PACKETS_PER_PARTICLE * 8);
    expect(high).toBeGreaterThan(low);
  });

  it('aggregates rather than drawing one object per packet', () => {
    // A flow at nearly five thousand packets per second must stay bounded.
    expect(particlesForPacketRate(4800)).toBeLessThanOrEqual(MAX_PARTICLES_PER_FLOW);
  });

  it('always shows at least one particle for an active flow', () => {
    expect(particlesForPacketRate(3)).toBe(1);
  });
});

// ------------------------------------------------------------------- store

describe('store ingests backend events', () => {
  beforeEach(() => {
    useQoSenseStore.setState({
      snapshot: null,
      series: [],
      flows: {},
      classifications: {},
      featureWindows: {},
      prediction: null,
      proposal: null,
      lastEnforcement: null,
      lastVerification: null,
      policyHistory: [],
      events: [],
      cues: [],
      intents: [],
    });
  });

  it('records a metrics snapshot into the series', () => {
    useQoSenseStore.getState().ingest(fx.event('metrics.snapshot', fx.snapshot()));
    const state = useQoSenseStore.getState();
    expect(state.snapshot?.rtt_ms).toBe(42);
    expect(state.series).toHaveLength(1);
  });

  it('keeps high-frequency snapshots out of the event timeline', () => {
    useQoSenseStore.getState().ingest(fx.event('metrics.snapshot', fx.snapshot()));
    expect(useQoSenseStore.getState().events).toHaveLength(0);
  });

  it('stores a classification and raises a scene cue for that flow', () => {
    useQoSenseStore.getState().ingest(
      fx.event('flow.classified', {
        window: {
          experiment_id: 'exp-0001',
          flow_id: 'call-down',
          device_id: 'laptop',
          observed_at: '2026-01-01T00:00:10Z',
          window_ms: 2000,
          direction: 'DOWNLOAD',
          protocol: 'UDP',
          feature_schema_version: 'flow-features-v1',
          features: {
            mean_packet_bytes: 760,
            std_packet_bytes: 90,
            min_packet_bytes: 600,
            max_packet_bytes: 900,
            mean_interarrival_ms: 1.9,
            std_interarrival_ms: 0.4,
            packets_per_second: 520,
            bytes_per_second: 400_000,
            flow_duration_ms: 10_000,
            up_bytes: 0,
            down_bytes: 800_000,
            up_packets: 0,
            down_packets: 1040,
            burst_count: 1,
            active_time_ratio: 1,
          },
        },
        classification: fx.classification(),
      }),
    );
    const state = useQoSenseStore.getState();
    expect(state.classifications['call-down'].predicted_class).toBe('INTERACTIVE');
    expect(state.cues.some((c) => c.kind === 'classified' && c.flowId === 'call-down')).toBe(true);
  });

  it('attaches an enforcement result to its own policy in the history', () => {
    const store = useQoSenseStore.getState();
    store.ingest(fx.event('policy.proposed', fx.proposal()));
    store.ingest(
      fx.event('policy.applied', {
        policy_id: 'policy-0007',
        transaction_id: 'txn-1',
        status: 'SIMULATED',
        data_mode: 'SIMULATION',
        requested_at: '2026-01-01T00:00:21Z',
        completed_at: '2026-01-01T00:00:21Z',
        apply_duration_ms: 0.4,
        snapshot_id: 'snap-1',
        read_back: null,
        error: null,
      }),
    );
    const history = useQoSenseStore.getState().policyHistory;
    expect(history).toHaveLength(1);
    expect(history[0].enforcement?.status).toBe('SIMULATED');
  });

  it('drops an intent lease from the active list once it expires', () => {
    const store = useQoSenseStore.getState();
    const lease = {
      intent_id: 'intent-0001',
      experiment_id: 'exp-0001',
      target_type: 'DEVICE',
      target_id: 'laptop',
      intent_type: 'PRIORITIZE_INTERACTIVE',
      reason: 'Work call',
      created_at: '2026-01-01T00:00:00Z',
      expires_at: '2026-01-01T00:05:00Z',
      status: 'ACTIVE',
      remaining_seconds: 300,
      rejection_reason: null,
    };
    store.ingest(fx.event('intent.updated', lease));
    expect(useQoSenseStore.getState().intents).toHaveLength(1);
    store.ingest(fx.event('intent.updated', { ...lease, status: 'EXPIRED' }));
    expect(useQoSenseStore.getState().intents).toHaveLength(0);
  });
});

// -------------------------------------------------------------- flow table

describe('flow table', () => {
  it('shows a rule-based label instead of a confidence number', () => {
    render(
      <FlowTable
        flows={[fx.flow()]}
        classifications={{ 'call-down': fx.classification() }}
        selectedFlowId={null}
        onSelect={() => {}}
      />,
    );
    const label = screen.getByText('Rule-based');
    expect(label).toBeInTheDocument();
    // The confidence cell itself must hold no percentage: a rule set returns a
    // class, not a probability.
    const confidenceCell = label.closest('td');
    expect(confidenceCell?.textContent).toBe('Rule-based');
    expect(label.getAttribute('title')).toContain('not a probability distribution');
  });

  it('marks a safe fallback rather than guessing a class confidence', () => {
    render(
      <FlowTable
        flows={[fx.flow({ traffic_class: 'UNKNOWN', classification_source: 'SAFE_FALLBACK' })]}
        classifications={{
          'call-down': fx.classification({
            source: 'SAFE_FALLBACK',
            predicted_class: 'UNKNOWN',
            decision: 'SAFE_FALLBACK',
            reason: 'MODEL_NOT_READY',
          }),
        }}
        selectedFlowId={null}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText('Safe fallback')).toBeInTheDocument();
  });

  it('explains that there are no flows rather than rendering an empty grid', () => {
    render(
      <FlowTable flows={[]} classifications={{}} selectedFlowId={null} onSelect={() => {}} />,
    );
    expect(screen.getByText('No active flows.')).toBeInTheDocument();
  });
});

// ------------------------------------------------------------------ panels

describe('model panels report real state', () => {
  const untrainedModel1 = {
    key: 'traffic_classifier',
    model_name: 'traffic_classifier',
    display_name: 'Model 1 - Traffic classifier',
    purpose: 'Predicts a broad traffic behaviour class.',
    algorithm: 'LightGBM multiclass',
    target: 'INTERACTIVE, BULK, …',
    state: 'NOT_CONFIGURED' as const,
    version: 'not-trained',
    artifact_path: null,
    feature_schema_version: 'flow-features-v1',
    calibration_available: false,
    last_inference_at: null,
    inference_count: 0,
    implementation_phase: 'Phase 4',
    round_allocation: 'Round 1',
    detail: 'No artifact configured. This model is not producing any prediction.',
  };

  it('does not show an untrained model as active', () => {
    render(
      <TrafficClassifierPanel
        status={untrainedModel1}
        classification={fx.classification()}
        flow={fx.flow()}
      />,
    );
    expect(screen.getByText('Not configured')).toBeInTheDocument();
    expect(screen.getByText(/not producing any prediction/)).toBeInTheDocument();
  });

  it('says a rule set produced the class, not the model', () => {
    render(
      <TrafficClassifierPanel
        status={untrainedModel1}
        classification={fx.classification()}
        flow={fx.flow()}
      />,
    );
    expect(screen.getByText('Deterministic rule set')).toBeInTheDocument();
    expect(screen.getByText('Not produced')).toBeInTheDocument();
  });

  it('keeps measured congestion separate from predicted risk', () => {
    render(
      <CongestionPredictorPanel
        status={{
          ...untrainedModel1,
          key: 'congestion_predictor',
          display_name: 'Model 2 - Congestion-risk predictor',
          implementation_phase: 'Phase 5',
          round_allocation: 'Round 2',
          feature_schema_version: null,
        }}
        prediction={{
          experiment_id: 'exp-0001',
          model_name: 'congestion_predictor',
          model_version: 'not-trained',
          window_start: null,
          window_end: null,
          prediction_horizon_seconds: null,
          congestion_probability: null,
          risk_band: 'UNAVAILABLE',
          decision: 'IGNORED',
          reason: 'MODEL_NOT_READY',
          inference_ms: null,
        }}
        measured={fx.snapshot()}
      />,
    );
    expect(screen.getByText('Measured now')).toBeInTheDocument();
    expect(screen.getByText('Congested')).toBeInTheDocument();
    expect(screen.getByText('Predicted ahead')).toBeInTheDocument();
    expect(screen.getByText('Unavailable')).toBeInTheDocument();
    expect(screen.getByText(/never derived from one another/)).toBeInTheDocument();
  });
});

describe('priority decision centre', () => {
  it('shows every candidate with its computed cost', () => {
    render(<PriorityDecisionCentre inputs={null} proposal={fx.proposal()} />);
    expect(screen.getByText('Protect interactive')).toBeInTheDocument();
    expect(screen.getByText('0.017')).toBeInTheDocument();
  });

  it('says a candidate has no score rather than showing a fabricated one', () => {
    render(<PriorityDecisionCentre inputs={null} proposal={fx.proposal()} />);
    expect(screen.getByText('no score')).toBeInTheDocument();
    expect(screen.getByText(/No allocation available/i)).toBeInTheDocument();
  });

  it('explains that the controller is idle in the reference mode', () => {
    render(<PriorityDecisionCentre inputs={null} proposal={null} />);
    expect(screen.getByText('The policy engine has not evaluated yet.')).toBeInTheDocument();
  });
});

describe('enforcement panel', () => {
  it('labels a simulated application as simulated, never as applied', () => {
    render(
      <EnforcementPanel
        enforcement={{
          policy_id: 'policy-0007',
          transaction_id: 'txn-1',
          status: 'SIMULATED',
          data_mode: 'SIMULATION',
          requested_at: '2026-01-01T00:00:21Z',
          completed_at: '2026-01-01T00:00:21Z',
          apply_duration_ms: 0.42,
          snapshot_id: 'snap-0001',
          read_back: {
            data_mode: 'SIMULATION',
            source: 'deterministic simulator shaping state',
            matches_intent: true,
            upload_path: ['device', 'Hierarchical Token Bucket class'],
            download_path: ['wide area network', 'Intermediate Functional Block'],
            observed_class_rates_mbps: { INTERACTIVE: 4, BULK: 55 },
            mismatch_detail: null,
          },
          error: null,
        }}
        proposal={fx.proposal()}
        installedRates={{ INTERACTIVE: 4, BULK: 55 }}
      />,
    );
    expect(screen.getByText('Simulated')).toBeInTheDocument();
    expect(screen.queryByText('Applied')).not.toBeInTheDocument();
    expect(screen.getByText(/not to Linux traffic control/)).toBeInTheDocument();
  });

  it('says "Not configured" when no policy rates exist', () => {
    render(<EnforcementPanel enforcement={null} proposal={null} installedRates={{}} />);
    expect(screen.getByText(/Not configured/)).toBeInTheDocument();
  });

  it('shows the download shaping path through the virtual interface', () => {
    render(
      <EnforcementPanel
        enforcement={{
          policy_id: 'p',
          transaction_id: 't',
          status: 'SIMULATED',
          data_mode: 'SIMULATION',
          requested_at: '2026-01-01T00:00:21Z',
          completed_at: '2026-01-01T00:00:21Z',
          apply_duration_ms: 1,
          snapshot_id: 's',
          read_back: {
            data_mode: 'SIMULATION',
            source: 'simulator',
            matches_intent: true,
            upload_path: ['device', 'gateway'],
            download_path: ['wide area network', 'redirect to Intermediate Functional Block'],
            observed_class_rates_mbps: {},
            mismatch_detail: null,
          },
          error: null,
        }}
        proposal={null}
        installedRates={{}}
      />,
    );
    expect(
      screen.getByText('redirect to Intermediate Functional Block'),
    ).toBeInTheDocument();
  });
});

// -------------------------------------------------------- two-dimensional view

describe('two-dimensional fallback', () => {
  it('renders the same facts as the scene without WebGL', () => {
    const { container } = render(
      <Topology2D
        topology={fx.topology()}
        flows={[fx.flow()]}
        snapshot={fx.snapshot()}
        selectedDeviceId={null}
        onSelectDevice={() => {}}
      />,
    );
    const svg = container.querySelector('svg');
    expect(svg).toBeTruthy();
    expect(within(svg as unknown as HTMLElement).getByText('Laptop')).toBeInTheDocument();
    expect(within(svg as unknown as HTMLElement).getByText('QoSense Gateway')).toBeInTheDocument();
    expect(within(svg as unknown as HTMLElement).getByText('Internet')).toBeInTheDocument();
  });

  it('shows the measured queue occupancy', () => {
    const { container } = render(
      <Topology2D
        topology={fx.topology()}
        flows={[fx.flow()]}
        snapshot={fx.snapshot({ queue_occupancy_percent: 72 })}
        selectedDeviceId={null}
        onSelectDevice={() => {}}
      />,
    );
    expect(container.textContent).toContain('72%');
  });

  it('explains when there is no topology rather than rendering an empty canvas', () => {
    render(
      <Topology2D
        topology={null}
        flows={[]}
        snapshot={null}
        selectedDeviceId={null}
        onSelectDevice={() => {}}
      />,
    );
    expect(screen.getByText('No topology loaded yet.')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------- timeline

describe('event timeline', () => {
  it('lists events newest first', () => {
    render(
      <EventTimeline
        events={[
          fx.event('flow.detected', {}, { summary: 'first', event_id: 'a' }),
          fx.event('policy.applied', {}, { summary: 'second', event_id: 'b' }),
        ]}
      />,
    );
    const rows = screen.getAllByText(/first|second/);
    expect(rows[0].textContent).toContain('second');
  });

  it('can filter to a subset of event types', () => {
    render(
      <EventTimeline
        events={[
          fx.event('flow.detected', {}, { summary: 'flow appeared', event_id: 'a' }),
          fx.event('rollback.started', {}, { summary: 'rolling back', event_id: 'b' }),
        ]}
        types={['rollback.started']}
      />,
    );
    expect(screen.getByText(/rolling back/)).toBeInTheDocument();
    expect(screen.queryByText(/flow appeared/)).not.toBeInTheDocument();
  });
});
