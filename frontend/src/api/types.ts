/**
 * TypeScript mirror of the backend contract in `docs/API_CONTRACT.md`.
 *
 * Field names and enum values match the Python models exactly. `null` means
 * "not measured" and must be rendered as such, never as a zero or a plausible
 * looking placeholder.
 */

export type DataMode = 'SIMULATION' | 'LIVE' | 'REPLAY';

export type ExperimentMode =
  | 'NO_QOS'
  | 'TRADITIONAL_ADAPTIVE'
  | 'CLASSIFIER_ONLY'
  | 'FULL_QOSENSE';

export type ExperimentStatus =
  | 'CREATED'
  | 'RUNNING'
  | 'PAUSED'
  | 'STOPPING'
  | 'COMPLETED'
  | 'STOPPED'
  | 'FAILED';

export type TrafficClass =
  | 'INTERACTIVE'
  | 'GAMING_LIKE'
  | 'STREAMING_LIKE'
  | 'BULK'
  | 'WEB_VOICE'
  | 'UNKNOWN';

export type Direction = 'UPLOAD' | 'DOWNLOAD';
export type Protocol = 'TCP' | 'UDP';

export type ModelState =
  | 'NOT_CONFIGURED'
  | 'TRAINING'
  | 'READY'
  | 'FAILED'
  | 'DISABLED';

export type ClassificationSource = 'MODEL_1' | 'DETERMINISTIC_RULES' | 'SAFE_FALLBACK';
export type CalibrationStatus = 'NOT_CALIBRATED' | 'CALIBRATED' | 'UNAVAILABLE';
export type ClassificationDecision = 'ACCEPTED' | 'SAFE_FALLBACK';
export type RiskBand = 'UNAVAILABLE' | 'LOW' | 'ELEVATED' | 'HIGH';
export type PredictionDecision = 'USED' | 'IGNORED';

export type CongestionState =
  | 'UNKNOWN'
  | 'CLEAR'
  | 'BUILDING'
  | 'CONGESTED'
  | 'BUFFERBLOAT';

export type DataQuality = 'NO_DATA' | 'STALE' | 'LOW_CONFIDENCE' | 'GOOD';

export type PolicyProfile =
  | 'KEEP_CURRENT'
  | 'PROTECT_INTERACTIVE'
  | 'LIMIT_BULK'
  | 'REBALANCE_CLASSES'
  | 'CONSERVATIVE_SAFE';

export type PolicyStatus = 'PROPOSED' | 'REJECTED' | 'ACCEPTED';

export type EnforcementStatus =
  | 'SIMULATED'
  | 'APPLIED'
  | 'REJECTED'
  | 'FAILED_ROLLED_BACK'
  | 'FAILED_SAFE_MODE';

export type VerificationOutcome = 'PENDING' | 'ACCEPTABLE' | 'DEGRADED' | 'INCONCLUSIVE';
export type IntentType = 'PRIORITIZE_INTERACTIVE' | 'PROTECT_DEVICE';
export type IntentStatus = 'ACTIVE' | 'EXPIRED' | 'CANCELLED' | 'REJECTED';

export type DeviceKind =
  | 'GATEWAY'
  | 'INTERNET'
  | 'LAPTOP'
  | 'GAMING_CONSOLE'
  | 'DESKTOP'
  | 'SMART_TV'
  | 'SMARTPHONE';

export type EventSeverity = 'INFO' | 'SUCCESS' | 'WARNING' | 'CRITICAL';

export type EventType =
  | 'experiment.status'
  | 'flow.detected'
  | 'flow.updated'
  | 'flow.classified'
  | 'capacity.updated'
  | 'congestion.predicted'
  | 'policy.evaluating'
  | 'policy.proposed'
  | 'policy.rejected'
  | 'policy.applied'
  | 'enforcement.verified'
  | 'metrics.snapshot'
  | 'verification.completed'
  | 'rollback.started'
  | 'rollback.completed'
  | 'watchdog.alert'
  | 'intent.updated'
  | 'packet.loss_observed'
  | 'experiment.completed'
  | 'system.error';

export interface EventEnvelope<P = Record<string, unknown>> {
  event_id: string;
  experiment_id: string | null;
  timestamp: string;
  data_mode: DataMode;
  type: EventType;
  sequence: number;
  severity: EventSeverity;
  summary: string;
  payload: P;
}

export interface DirectionMetrics {
  direction: Direction;
  offered_mbps: number;
  served_mbps: number;
  link_rate_mbps: number | null;
  utilization_percent: number | null;
  queue_bytes: number;
  queue_packets: number;
  queue_occupancy_percent: number;
  queue_delay_ms: number;
  dropped_packets: number;
}

export interface MetricsSnapshot {
  experiment_id: string | null;
  timestamp: string;
  elapsed_seconds: number;
  data_mode: DataMode;
  download_mbps: number | null;
  upload_mbps: number | null;
  usable_capacity_mbps: number | null;
  capacity_quality: DataQuality;
  rtt_ms: number | null;
  idle_baseline_rtt_ms: number | null;
  added_delay_ms: number | null;
  jitter_ms: number | null;
  packet_loss_percent: number | null;
  queue_packets: number | null;
  queue_occupancy_percent: number | null;
  active_flows: number;
  congestion_state: CongestionState;
  congestion_prediction: RiskBand;
  policy_id: string | null;
  policy_profile: string | null;
  per_direction: DirectionMetrics[];
}

export interface FlowState {
  flow_id: string;
  device_id: string;
  device_label: string;
  direction: Direction;
  protocol: Protocol;
  traffic_class: TrafficClass;
  classification_source: ClassificationSource;
  confidence: number | null;
  started_at: string;
  last_update: string;
  bytes_per_second: number;
  packets_per_second: number;
  demand_bytes_per_second: number;
  served_fraction: number;
  flow_rtt_ms: number | null;
  queue_class: string;
  data_mode: DataMode;
  total_bytes: number;
}

export interface FlowFeatures {
  mean_packet_bytes: number;
  std_packet_bytes: number;
  min_packet_bytes: number;
  max_packet_bytes: number;
  mean_interarrival_ms: number;
  std_interarrival_ms: number;
  packets_per_second: number;
  bytes_per_second: number;
  flow_duration_ms: number;
  up_bytes: number;
  down_bytes: number;
  up_packets: number;
  down_packets: number;
  burst_count: number;
  active_time_ratio: number;
}

export interface FlowFeatureWindow {
  experiment_id: string;
  flow_id: string;
  device_id: string;
  observed_at: string;
  window_ms: number;
  direction: Direction;
  protocol: Protocol;
  feature_schema_version: string;
  features: FlowFeatures;
}

export interface ClassificationResult {
  flow_id: string;
  model_name: string;
  model_version: string;
  source: ClassificationSource;
  predicted_class: TrafficClass;
  probabilities: Partial<Record<TrafficClass, number>>;
  calibrated_confidence: number | null;
  calibration_status: CalibrationStatus;
  decision: ClassificationDecision;
  reason: string;
  inference_ms: number | null;
  feature_schema_version: string;
}

export interface CongestionPrediction {
  experiment_id: string;
  model_name: string;
  model_version: string;
  window_start: string | null;
  window_end: string | null;
  prediction_horizon_seconds: number | null;
  congestion_probability: number | null;
  risk_band: RiskBand;
  decision: PredictionDecision;
  reason: string;
  inference_ms: number | null;
}

export interface CapacityEstimate {
  direction: Direction;
  usable_capacity_mbps: number | null;
  delivery_rate_sample_mbps: number | null;
  sample_count: number;
  sample_window_seconds: number;
  ewma_alpha: number;
  quality: DataQuality;
  saturated_observation: boolean;
  reason: string;
  updated_at: string | null;
  fallback_capacity_mbps: number | null;
}

export interface ClassAllocation {
  traffic_class: TrafficClass;
  ceiling_mbps: number;
  guaranteed_floor_mbps: number;
  share_of_capacity_percent: number | null;
}

export interface CandidateCost {
  profile: PolicyProfile;
  interactive_delay_risk: number | null;
  bulk_progress_risk: number | null;
  fairness_penalty: number | null;
  churn_penalty: number | null;
  total_cost: number | null;
  hard_constraint_violations: string[];
  eligible: boolean;
  notes: string[];
}

export interface SafetyCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface SafetyCheckResult {
  accepted: boolean;
  checks: SafetyCheck[];
  rejection_reasons: string[];
  evaluated_at: string;
}

export interface PolicyProposal {
  policy_id: string;
  experiment_id: string;
  profile: PolicyProfile;
  created_at: string;
  data_mode: DataMode;
  reason_codes: string[];
  explanation: string;
  source_event_ids: string[];
  class_allocations: ClassAllocation[];
  upload_class_allocations: ClassAllocation[];
  class_rates_mbps: Partial<Record<TrafficClass, number>>;
  candidates: CandidateCost[];
  weights_version: string;
  based_on_capacity_mbps: number | null;
  valid_until: string | null;
  status: PolicyStatus;
  safety: SafetyCheckResult | null;
}

export interface ReadBackState {
  data_mode: DataMode;
  source: string;
  matches_intent: boolean;
  upload_path: string[];
  download_path: string[];
  observed_class_rates_mbps: Partial<Record<TrafficClass, number>>;
  mismatch_detail: string | null;
}

export interface EnforcementResult {
  policy_id: string;
  transaction_id: string;
  status: EnforcementStatus;
  data_mode: DataMode;
  requested_at: string;
  completed_at: string;
  apply_duration_ms: number;
  snapshot_id: string | null;
  read_back: ReadBackState | null;
  error: string | null;
}

export interface VerificationResult {
  policy_id: string;
  outcome: VerificationOutcome;
  data_mode: DataMode;
  completed_at: string;
  measurement_window_seconds: number;
  before_added_delay_ms: number | null;
  after_added_delay_ms: number | null;
  before_jitter_ms: number | null;
  after_jitter_ms: number | null;
  before_packet_loss_percent: number | null;
  after_packet_loss_percent: number | null;
  before_queue_occupancy_percent: number | null;
  after_queue_occupancy_percent: number | null;
  before_bulk_goodput_mbps: number | null;
  after_bulk_goodput_mbps: number | null;
  criteria: SafetyCheck[];
  explanation: string;
}

export interface PolicyHistoryEntry {
  proposal: PolicyProposal;
  enforcement: EnforcementResult | null;
  verification: VerificationResult | null;
}

export interface IntentLease {
  intent_id: string;
  experiment_id: string | null;
  target_type: string;
  target_id: string;
  intent_type: IntentType;
  reason: string;
  created_at: string;
  expires_at: string;
  status: IntentStatus;
  remaining_seconds: number;
  rejection_reason: string | null;
}

export interface TopologyDevice {
  device_id: string;
  label: string;
  kind: DeviceKind;
  description: string;
  scene_x: number;
  scene_z: number;
}

export interface TopologyLink {
  link_id: string;
  source_id: string;
  target_id: string;
  description: string;
}

export interface Topology {
  topology_id: string;
  data_mode: DataMode;
  description: string;
  devices: TopologyDevice[];
  links: TopologyLink[];
}

export interface ParameterBound {
  name: string;
  label: string;
  unit: string;
  minimum: number;
  maximum: number;
  default: number;
  description: string;
}

export interface ScenarioDefinition {
  scenario_id: string;
  name: string;
  summary: string;
  is_laboratory_emulation: boolean;
  default_duration_seconds: number;
  default_download_mbps: number;
  default_upload_mbps: number;
  default_base_rtt_ms: number;
  queue_limit_packets: number;
  parameters: ParameterBound[];
}

export interface ModeAvailability {
  mode: ExperimentMode;
  available: boolean;
  degraded: boolean;
  requires: string[];
  detail: string;
}

export interface ExperimentConfig {
  experiment_id: string;
  scenario_id: string;
  scenario_name: string;
  mode: ExperimentMode;
  data_mode: DataMode;
  seed: number;
  duration_seconds: number;
  tick_seconds: number;
  parameters: Record<string, number>;
  repeat_label: string | null;
  created_at: string;
  weights_version: string;
  feature_schema_version: string;
  model_versions: Record<string, string>;
}

export interface ExperimentState {
  config: ExperimentConfig;
  status: ExperimentStatus;
  mode_availability: ModeAvailability;
  started_at: string | null;
  stopped_at: string | null;
  elapsed_seconds: number;
  tick_count: number;
  event_count: number;
  last_sequence: number;
  notes: string[];
}

export interface MetricSummary {
  metric: string;
  unit: string;
  samples: number;
  median: number | null;
  p25: number | null;
  p75: number | null;
  minimum: number | null;
  maximum: number | null;
}

export interface ExperimentReport {
  experiment_id: string;
  config: ExperimentConfig;
  status: ExperimentStatus;
  data_mode: DataMode;
  generated_at: string;
  duration_observed_seconds: number;
  sample_count: number;
  summaries: MetricSummary[];
  jain_fairness_index: number | null;
  fairness_basis: string;
  policy_changes: number;
  safety_rejections: number;
  rollbacks: number;
  zero_progress_windows: number;
  bulk_transferred_megabytes: number | null;
  limitations: string[];
}

export interface ModelStatus {
  key: string;
  model_name: string;
  display_name: string;
  purpose: string;
  algorithm: string;
  target: string;
  state: ModelState;
  version: string;
  artifact_path: string | null;
  feature_schema_version: string | null;
  calibration_available: boolean;
  last_inference_at: string | null;
  inference_count: number;
  implementation_phase: string;
  round_allocation: string;
  detail: string;
}

export interface LabAgentStatus {
  configured: boolean;
  reachable: boolean;
  live_enforcement_enabled: boolean;
  detail: string;
}

export interface SystemStatus {
  build_version: string;
  data_mode: DataMode;
  supported_data_modes: DataMode[];
  controller_running: boolean;
  models: ModelStatus[];
  lab_agent: LabAgentStatus;
  modes: ModeAvailability[];
  active_experiment_id: string | null;
  experiment: ExperimentState | null;
  watchdog_last_heartbeat: string | null;
  watchdog_alerts: number;
  subscriber_count: number;
  server_time: string;
}

export interface QoSControllerView {
  experiment_id: string | null;
  data_mode: DataMode;
  download_capacity: CapacityEstimate | null;
  upload_capacity: CapacityEstimate | null;
  measured_congestion: CongestionState;
  predicted_congestion: CongestionPrediction;
  current_profile: string | null;
  current_proposal: PolicyProposal | null;
  installed_class_rates_mbps: Record<string, number>;
  active_intents: IntentLease[];
  history: PolicyHistoryEntry[];
}

export interface EventPage {
  events: EventEnvelope[];
  total: number;
  dropped: number;
}

export interface ExperimentCreateRequest {
  scenario_id: string;
  mode: ExperimentMode;
  data_mode?: DataMode;
  seed?: number;
  duration_seconds?: number | null;
  parameters?: Record<string, number>;
  repeat_label?: string | null;
}
