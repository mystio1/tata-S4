/** Shared test fixtures shaped exactly like the backend's responses. */

import type {
  ClassificationResult,
  EventEnvelope,
  FlowState,
  MetricsSnapshot,
  PolicyProposal,
  Topology,
} from '@/api/types';

export function snapshot(overrides: Partial<MetricsSnapshot> = {}): MetricsSnapshot {
  return {
    experiment_id: 'exp-0001',
    timestamp: '2026-01-01T00:00:10Z',
    elapsed_seconds: 10,
    data_mode: 'SIMULATION',
    download_mbps: 68.4,
    upload_mbps: 12.6,
    usable_capacity_mbps: 92.3,
    capacity_quality: 'GOOD',
    rtt_ms: 42,
    idle_baseline_rtt_ms: 18,
    added_delay_ms: 24,
    jitter_ms: 12,
    packet_loss_percent: 0.8,
    queue_packets: 280,
    queue_occupancy_percent: 72,
    active_flows: 5,
    congestion_state: 'CONGESTED',
    congestion_prediction: 'UNAVAILABLE',
    policy_id: null,
    policy_profile: null,
    per_direction: [
      {
        direction: 'DOWNLOAD',
        offered_mbps: 107,
        served_mbps: 100,
        link_rate_mbps: null,
        utilization_percent: null,
        queue_bytes: 392_000,
        queue_packets: 280,
        queue_occupancy_percent: 72,
        queue_delay_ms: 31.4,
        dropped_packets: 4,
      },
      {
        direction: 'UPLOAD',
        offered_mbps: 2,
        served_mbps: 2,
        link_rate_mbps: null,
        utilization_percent: null,
        queue_bytes: 0,
        queue_packets: 0,
        queue_occupancy_percent: 0,
        queue_delay_ms: 0,
        dropped_packets: 0,
      },
    ],
    ...overrides,
  };
}

export function flow(overrides: Partial<FlowState> = {}): FlowState {
  return {
    flow_id: 'call-down',
    device_id: 'laptop',
    device_label: 'Laptop',
    direction: 'DOWNLOAD',
    protocol: 'UDP',
    traffic_class: 'INTERACTIVE',
    classification_source: 'DETERMINISTIC_RULES',
    confidence: null,
    started_at: '2026-01-01T00:00:00Z',
    last_update: '2026-01-01T00:00:10Z',
    bytes_per_second: 400_000,
    packets_per_second: 520,
    demand_bytes_per_second: 400_000,
    served_fraction: 1,
    flow_rtt_ms: 42,
    queue_class: 'INTERACTIVE',
    data_mode: 'SIMULATION',
    total_bytes: 4_000_000,
    ...overrides,
  };
}

export function classification(
  overrides: Partial<ClassificationResult> = {},
): ClassificationResult {
  return {
    flow_id: 'call-down',
    model_name: 'traffic_classifier',
    model_version: 'deterministic-rules-v1',
    source: 'DETERMINISTIC_RULES',
    predicted_class: 'INTERACTIVE',
    probabilities: {},
    calibrated_confidence: null,
    calibration_status: 'UNAVAILABLE',
    decision: 'ACCEPTED',
    reason: 'RULE_INTERACTIVE_STEADY_DATAGRAMS',
    inference_ms: 0.004,
    feature_schema_version: 'flow-features-v1',
    ...overrides,
  };
}

export function proposal(overrides: Partial<PolicyProposal> = {}): PolicyProposal {
  return {
    policy_id: 'policy-0007',
    experiment_id: 'exp-0001',
    profile: 'PROTECT_INTERACTIVE',
    created_at: '2026-01-01T00:00:21Z',
    data_mode: 'SIMULATION',
    reason_codes: ['SELECTED_PROTECT_INTERACTIVE', 'ADDED_DELAY_ABOVE_TARGET'],
    explanation: 'Selected PROTECT_INTERACTIVE with a computed cost of 0.017.',
    source_event_ids: ['evt-000101'],
    class_allocations: [
      {
        traffic_class: 'INTERACTIVE',
        ceiling_mbps: 4,
        guaranteed_floor_mbps: 3.2,
        share_of_capacity_percent: 4.2,
      },
      {
        traffic_class: 'BULK',
        ceiling_mbps: 55,
        guaranteed_floor_mbps: 2,
        share_of_capacity_percent: 57.9,
      },
    ],
    upload_class_allocations: [],
    class_rates_mbps: { INTERACTIVE: 4, BULK: 55 },
    candidates: [
      {
        profile: 'KEEP_CURRENT',
        interactive_delay_risk: null,
        bulk_progress_risk: null,
        fairness_penalty: null,
        churn_penalty: null,
        total_cost: null,
        hard_constraint_violations: ['NO_ALLOCATION_AVAILABLE'],
        eligible: false,
        notes: [],
      },
      {
        profile: 'PROTECT_INTERACTIVE',
        interactive_delay_risk: 0,
        bulk_progress_risk: 0.064,
        fairness_penalty: null,
        churn_penalty: 0,
        total_cost: 0.017,
        hard_constraint_violations: [],
        eligible: true,
        notes: ['Interactive risk projected from ceilings.'],
      },
    ],
    weights_version: 'qosense-policy-weights-v1',
    based_on_capacity_mbps: 95,
    valid_until: null,
    status: 'ACCEPTED',
    safety: {
      accepted: true,
      checks: [
        { name: 'capacity_fresh', passed: true, detail: 'Capacity estimate quality is GOOD.' },
        { name: 'bulk_minimum_service', passed: true, detail: 'Bulk ceiling is 55.00 Mbps.' },
      ],
      rejection_reasons: [],
      evaluated_at: '2026-01-01T00:00:21Z',
    },
    ...overrides,
  };
}

export function topology(): Topology {
  return {
    topology_id: 'iso_during_call-topology',
    data_mode: 'SIMULATION',
    description: 'Emulated home topology.',
    devices: [
      {
        device_id: 'gateway',
        label: 'QoSense Gateway',
        kind: 'GATEWAY',
        description: 'Shaping point under test',
        scene_x: 0,
        scene_z: 0,
      },
      {
        device_id: 'internet',
        label: 'Internet',
        kind: 'INTERNET',
        description: 'Wide area network peer',
        scene_x: 0,
        scene_z: -5.4,
      },
      {
        device_id: 'laptop',
        label: 'Laptop',
        kind: 'LAPTOP',
        description: 'Video call endpoint',
        scene_x: -4.6,
        scene_z: 1.2,
      },
    ],
    links: [
      {
        link_id: 'wan',
        source_id: 'gateway',
        target_id: 'internet',
        description: 'Wide area network link',
      },
    ],
  };
}

export function event<T>(
  type: EventEnvelope['type'],
  payload: T,
  overrides: Partial<EventEnvelope> = {},
): EventEnvelope {
  return {
    event_id: `evt-${Math.random().toString(36).slice(2, 8)}`,
    experiment_id: 'exp-0001',
    timestamp: '2026-01-01T00:00:10Z',
    data_mode: 'SIMULATION',
    type,
    sequence: 1,
    severity: 'INFO',
    summary: '',
    payload: payload as Record<string, unknown>,
    ...overrides,
  };
}
