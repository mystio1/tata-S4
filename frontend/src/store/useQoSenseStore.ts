/**
 * Normalised application state.
 *
 * Everything the dashboard and the three-dimensional scene draw comes from
 * here, and everything here comes from a backend event or response. No widget
 * generates its own numbers, so the charts, the panels and the scene cannot
 * disagree with each other.
 */

import { create } from 'zustand';

import { ApiError, api, experimentSocketUrl } from '@/api/client';
import type {
  ClassificationResult,
  CongestionPrediction,
  EnforcementResult,
  EventEnvelope,
  ExperimentCreateRequest,
  ExperimentMode,
  ExperimentReport,
  ExperimentState,
  FlowFeatureWindow,
  FlowState,
  IntentLease,
  IntentType,
  MetricsSnapshot,
  ModelStatus,
  PolicyHistoryEntry,
  PolicyProposal,
  ScenarioDefinition,
  SystemStatus,
  Topology,
  TrafficClass,
  VerificationResult,
} from '@/api/types';

/** How many metric samples the charts keep. At 5 per second this is 2 minutes. */
const SERIES_LIMIT = 600;
/** How many events the timeline keeps in memory. */
const EVENT_LIMIT = 400;

export type PageId =
  | 'overview'
  | 'traffic'
  | 'intelligence'
  | 'controller'
  | 'experiments'
  | 'performance'
  | 'safety'
  | 'settings';

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error';

export interface PolicyEvaluationInputs {
  elapsed_seconds: number;
  class_demand_mbps: Record<string, number>;
  usable_capacity_mbps: number | null;
  capacity_quality: string;
  added_delay_ms: number;
  jitter_ms: number;
  packet_loss_percent: number;
  queue_occupancy_percent: number;
  congestion_state: string;
  congestion_prediction: CongestionPrediction;
  classification_evidence: boolean;
  active_intents: IntentLease[];
  current_profile: string | null;
  seconds_since_policy_change: number;
}

/** A short-lived cue the scene plays in response to a backend event. */
export interface SceneCue {
  id: string;
  kind: 'loss' | 'classified' | 'policy' | 'rollback';
  flowId?: string;
  at: number;
}

interface QoSenseState {
  // Connection and bootstrap
  connection: ConnectionStatus;
  connectionError: string | null;
  droppedEvents: number;
  bootstrapped: boolean;
  bootstrapError: string | null;

  // Static and slow-moving
  system: SystemStatus | null;
  scenarios: ScenarioDefinition[];
  topology: Topology | null;

  // Experiment
  experiment: ExperimentState | null;
  experiments: ExperimentState[];
  report: ExperimentReport | null;
  busy: boolean;
  actionError: string | null;

  // Telemetry
  snapshot: MetricsSnapshot | null;
  series: MetricsSnapshot[];
  flows: Record<string, FlowState>;
  classifications: Record<string, ClassificationResult>;
  featureWindows: Record<string, FlowFeatureWindow>;
  prediction: CongestionPrediction | null;

  // Control plane
  evaluationInputs: PolicyEvaluationInputs | null;
  proposal: PolicyProposal | null;
  lastEnforcement: EnforcementResult | null;
  lastVerification: VerificationResult | null;
  policyHistory: PolicyHistoryEntry[];
  intents: IntentLease[];

  // Events and scene cues
  events: EventEnvelope[];
  cues: SceneCue[];

  // Interface preferences
  page: PageId;
  selectedFlowId: string | null;
  selectedDeviceId: string | null;
  prefer2D: boolean;
  webglFailed: boolean;

  // Actions
  bootstrap: () => Promise<void>;
  setPage: (page: PageId) => void;
  selectFlow: (flowId: string | null) => void;
  selectDevice: (deviceId: string | null) => void;
  setPrefer2D: (value: boolean) => void;
  reportWebglFailure: () => void;

  createExperiment: (request: ExperimentCreateRequest) => Promise<ExperimentState | null>;
  startExperiment: () => Promise<void>;
  pauseExperiment: () => Promise<void>;
  resumeExperiment: () => Promise<void>;
  stopExperiment: () => Promise<void>;
  resetExperiment: () => Promise<void>;
  refreshReport: () => Promise<void>;
  refreshPolicy: () => Promise<void>;

  createIntent: (input: {
    targetId: string;
    intentType: IntentType;
    durationSeconds: number;
    reason: string;
  }) => Promise<void>;
  cancelIntent: (intentId: string) => Promise<void>;

  attachTo: (experimentId: string) => void;
  disconnect: () => void;
  ingest: (event: EventEnvelope) => void;
  clearActionError: () => void;
}

let socket: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectAttempts = 0;
let cueCounter = 0;

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

function pushCue(cues: SceneCue[], cue: Omit<SceneCue, 'id' | 'at'>): SceneCue[] {
  cueCounter += 1;
  const next = [...cues, { ...cue, id: `cue-${cueCounter}`, at: Date.now() }];
  return next.slice(-24);
}

export const useQoSenseStore = create<QoSenseState>((set, get) => ({
  connection: 'idle',
  connectionError: null,
  droppedEvents: 0,
  bootstrapped: false,
  bootstrapError: null,

  system: null,
  scenarios: [],
  topology: null,

  experiment: null,
  experiments: [],
  report: null,
  busy: false,
  actionError: null,

  snapshot: null,
  series: [],
  flows: {},
  classifications: {},
  featureWindows: {},
  prediction: null,

  evaluationInputs: null,
  proposal: null,
  lastEnforcement: null,
  lastVerification: null,
  policyHistory: [],
  intents: [],

  events: [],
  cues: [],

  page: 'overview',
  selectedFlowId: null,
  selectedDeviceId: null,
  prefer2D: false,
  webglFailed: false,

  // ---------------------------------------------------------------- setup

  bootstrap: async () => {
    try {
      const [system, scenarios, experiments] = await Promise.all([
        api.systemStatus(),
        api.scenarios(),
        api.listExperiments(),
      ]);
      set({ system, scenarios, experiments, bootstrapped: true, bootstrapError: null });

      const active = system.active_experiment_id;
      if (active) {
        set({ experiment: system.experiment });
        get().attachTo(active);
        await Promise.allSettled([
          api.topology(active).then((topology) => set({ topology })),
          api.flows(active).then((flows) =>
            set({ flows: Object.fromEntries(flows.map((f) => [f.flow_id, f])) }),
          ),
          get().refreshPolicy(),
        ]);
      }
    } catch (error) {
      set({ bootstrapError: errorMessage(error), bootstrapped: true });
    }
  },

  setPage: (page) => set({ page }),
  selectFlow: (selectedFlowId) => set({ selectedFlowId }),
  selectDevice: (selectedDeviceId) => set({ selectedDeviceId }),
  setPrefer2D: (prefer2D) => set({ prefer2D }),
  // Idempotent: repeated reports must not keep producing store updates.
  reportWebglFailure: () => {
    if (!get().webglFailed) set({ webglFailed: true });
  },
  clearActionError: () => set({ actionError: null }),

  // ----------------------------------------------------------- experiment

  createExperiment: async (request) => {
    set({ busy: true, actionError: null });
    try {
      const experiment = await api.createExperiment(request);
      const id = experiment.config.experiment_id;
      const [topology, experiments] = await Promise.all([
        api.topology(id),
        api.listExperiments(),
      ]);
      set({
        experiment,
        experiments,
        topology,
        report: null,
        snapshot: null,
        series: [],
        flows: {},
        classifications: {},
        featureWindows: {},
        prediction: null,
        evaluationInputs: null,
        proposal: null,
        lastEnforcement: null,
        lastVerification: null,
        policyHistory: [],
        intents: [],
        events: [],
        cues: [],
        droppedEvents: 0,
        selectedFlowId: null,
      });
      get().attachTo(id);
      return experiment;
    } catch (error) {
      set({ actionError: errorMessage(error) });
      return null;
    } finally {
      set({ busy: false });
    }
  },

  startExperiment: async () => {
    const id = get().experiment?.config.experiment_id;
    if (!id) return;
    set({ busy: true, actionError: null });
    try {
      set({ experiment: await api.startExperiment(id) });
      get().attachTo(id);
    } catch (error) {
      set({ actionError: errorMessage(error) });
    } finally {
      set({ busy: false });
    }
  },

  pauseExperiment: async () => {
    const id = get().experiment?.config.experiment_id;
    if (!id) return;
    set({ busy: true, actionError: null });
    try {
      set({ experiment: await api.pauseExperiment(id) });
    } catch (error) {
      set({ actionError: errorMessage(error) });
    } finally {
      set({ busy: false });
    }
  },

  resumeExperiment: async () => {
    const id = get().experiment?.config.experiment_id;
    if (!id) return;
    set({ busy: true, actionError: null });
    try {
      set({ experiment: await api.resumeExperiment(id) });
    } catch (error) {
      set({ actionError: errorMessage(error) });
    } finally {
      set({ busy: false });
    }
  },

  stopExperiment: async () => {
    const id = get().experiment?.config.experiment_id;
    if (!id) return;
    set({ busy: true, actionError: null });
    try {
      set({ experiment: await api.stopExperiment(id) });
      await get().refreshReport();
    } catch (error) {
      set({ actionError: errorMessage(error) });
    } finally {
      set({ busy: false });
    }
  },

  resetExperiment: async () => {
    const id = get().experiment?.config.experiment_id;
    if (!id) return;
    set({ busy: true, actionError: null });
    try {
      const experiment = await api.resetExperiment(id);
      const nextId = experiment.config.experiment_id;
      set({
        experiment,
        report: null,
        snapshot: null,
        series: [],
        flows: {},
        classifications: {},
        featureWindows: {},
        prediction: null,
        evaluationInputs: null,
        proposal: null,
        lastEnforcement: null,
        lastVerification: null,
        policyHistory: [],
        intents: [],
        events: [],
        cues: [],
        droppedEvents: 0,
        experiments: await api.listExperiments(),
      });
      get().attachTo(nextId);
    } catch (error) {
      set({ actionError: errorMessage(error) });
    } finally {
      set({ busy: false });
    }
  },

  refreshReport: async () => {
    const id = get().experiment?.config.experiment_id;
    if (!id) return;
    try {
      set({ report: await api.report(id) });
    } catch (error) {
      set({ actionError: errorMessage(error) });
    }
  },

  refreshPolicy: async () => {
    const id = get().experiment?.config.experiment_id;
    if (!id) return;
    try {
      const view = await api.currentPolicy(id);
      set({
        proposal: view.current_proposal ?? get().proposal,
        prediction: view.predicted_congestion,
        policyHistory: view.history,
        intents: view.active_intents,
      });
    } catch {
      // A policy view is optional context; failing to fetch it must not break
      // the page that asked for it.
    }
  },

  // --------------------------------------------------------------- intent

  createIntent: async (input) => {
    const id = get().experiment?.config.experiment_id ?? null;
    set({ busy: true, actionError: null });
    try {
      await api.createIntent({
        target_type: 'DEVICE',
        target_id: input.targetId,
        intent_type: input.intentType,
        duration_seconds: input.durationSeconds,
        reason: input.reason,
        experiment_id: id,
      });
      await get().refreshPolicy();
    } catch (error) {
      set({ actionError: errorMessage(error) });
    } finally {
      set({ busy: false });
    }
  },

  cancelIntent: async (intentId) => {
    const id = get().experiment?.config.experiment_id ?? null;
    set({ busy: true, actionError: null });
    try {
      await api.cancelIntent(intentId, id);
      await get().refreshPolicy();
    } catch (error) {
      set({ actionError: errorMessage(error) });
    } finally {
      set({ busy: false });
    }
  },

  // ------------------------------------------------------------ streaming

  attachTo: (experimentId) => {
    get().disconnect();
    set({ connection: 'connecting', connectionError: null });

    const open = () => {
      const next = new WebSocket(experimentSocketUrl(experimentId));
      socket = next;

      next.onopen = () => {
        reconnectAttempts = 0;
        set({ connection: 'open', connectionError: null });
      };

      next.onmessage = (message) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(message.data as string);
        } catch {
          return;
        }
        if (!parsed || typeof parsed !== 'object') return;
        if ((parsed as { type?: string }).type === 'keepalive') {
          const dropped = (parsed as { dropped?: number }).dropped ?? 0;
          if (dropped > get().droppedEvents) set({ droppedEvents: dropped });
          return;
        }
        get().ingest(parsed as EventEnvelope);
      };

      next.onerror = () => {
        set({ connection: 'error', connectionError: 'The event stream reported an error.' });
      };

      next.onclose = () => {
        if (socket !== next) return;
        socket = null;
        set({ connection: 'closed' });
        // Back off up to roughly eight seconds, then keep retrying at that
        // interval so a restarted backend is picked up without a page reload.
        reconnectAttempts += 1;
        const delay = Math.min(8000, 400 * 2 ** Math.min(reconnectAttempts, 5));
        reconnectTimer = setTimeout(open, delay);
      };
    };

    open();
  },

  disconnect: () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    reconnectAttempts = 0;
    if (socket) {
      const closing = socket;
      socket = null;
      closing.onclose = null;
      closing.close();
    }
    set({ connection: 'idle' });
  },

  // -------------------------------------------------------------- ingest

  ingest: (event) => {
    const state = get();
    const patch: Partial<QoSenseState> = {};

    // Metrics snapshots are high frequency and carry no narrative, so they are
    // kept out of the event timeline.
    if (event.type !== 'metrics.snapshot') {
      patch.events = [...state.events, event].slice(-EVENT_LIMIT);
    }

    switch (event.type) {
      case 'metrics.snapshot': {
        const snapshot = event.payload as unknown as MetricsSnapshot;
        patch.snapshot = snapshot;
        patch.series = [...state.series, snapshot].slice(-SERIES_LIMIT);
        break;
      }

      case 'flow.detected': {
        const flow = event.payload as unknown as FlowState;
        patch.flows = { ...state.flows, [flow.flow_id]: flow };
        break;
      }

      case 'flow.classified': {
        const payload = event.payload as unknown as {
          window: FlowFeatureWindow;
          classification: ClassificationResult;
        };
        patch.classifications = {
          ...state.classifications,
          [payload.classification.flow_id]: payload.classification,
        };
        patch.featureWindows = {
          ...state.featureWindows,
          [payload.window.flow_id]: payload.window,
        };
        patch.cues = pushCue(state.cues, {
          kind: 'classified',
          flowId: payload.classification.flow_id,
        });
        break;
      }

      case 'congestion.predicted': {
        patch.prediction = event.payload as unknown as CongestionPrediction;
        break;
      }

      case 'policy.evaluating': {
        patch.evaluationInputs = event.payload as unknown as PolicyEvaluationInputs;
        break;
      }

      case 'policy.proposed':
      case 'policy.rejected': {
        const proposal = event.payload as unknown as PolicyProposal;
        patch.proposal = proposal;
        patch.policyHistory = [
          ...state.policyHistory.filter((e) => e.proposal.policy_id !== proposal.policy_id),
          { proposal, enforcement: null, verification: null },
        ].slice(-80);
        break;
      }

      case 'policy.applied': {
        const enforcement = event.payload as unknown as EnforcementResult;
        patch.lastEnforcement = enforcement;
        patch.policyHistory = state.policyHistory.map((entry) =>
          entry.proposal.policy_id === enforcement.policy_id
            ? { ...entry, enforcement }
            : entry,
        );
        patch.cues = pushCue(state.cues, { kind: 'policy' });
        break;
      }

      case 'verification.completed': {
        const verification = event.payload as unknown as VerificationResult;
        patch.lastVerification = verification;
        patch.policyHistory = state.policyHistory.map((entry) =>
          entry.proposal.policy_id === verification.policy_id
            ? { ...entry, verification }
            : entry,
        );
        break;
      }

      case 'rollback.completed': {
        patch.cues = pushCue(state.cues, { kind: 'rollback' });
        break;
      }

      case 'packet.loss_observed': {
        patch.cues = pushCue(state.cues, { kind: 'loss' });
        break;
      }

      case 'intent.updated': {
        const lease = event.payload as unknown as IntentLease;
        const others = state.intents.filter((i) => i.intent_id !== lease.intent_id);
        patch.intents = lease.status === 'ACTIVE' ? [...others, lease] : others;
        break;
      }

      case 'experiment.status':
      case 'experiment.completed': {
        const payload = event.payload as { state?: ExperimentState; status?: string };
        if (payload.state) patch.experiment = payload.state;
        else if (payload.status && state.experiment) {
          patch.experiment = {
            ...state.experiment,
            status: payload.status as ExperimentState['status'],
          };
        }
        if (event.type === 'experiment.completed') {
          const report = (event.payload as { report?: ExperimentReport }).report;
          if (report) patch.report = report;
        }
        break;
      }

      default:
        break;
    }

    set(patch);
  },
}));

// --------------------------------------------------------------- selectors
//
// A selector that builds a new array or object on every call breaks the
// reference check `useSyncExternalStore` performs during render, which sends
// React into a re-render loop. Selectors that derive a collection are
// therefore wrapped in `useShallow` at the call site, and the ones that can
// return "nothing" hand back a shared constant rather than a fresh literal.

const NO_MODELS: ModelStatus[] = [];

export function selectFlowList(state: QoSenseState): FlowState[] {
  return Object.values(state.flows).sort((a, b) => a.flow_id.localeCompare(b.flow_id));
}

export function selectModels(state: QoSenseState): ModelStatus[] {
  return state.system?.models ?? NO_MODELS;
}

export function selectClassDemand(state: QoSenseState): Record<TrafficClass, number> {
  const totals = {} as Record<TrafficClass, number>;
  for (const flow of Object.values(state.flows)) {
    const mbps = (flow.bytes_per_second * 8) / 1_000_000;
    totals[flow.traffic_class] = (totals[flow.traffic_class] ?? 0) + mbps;
  }
  return totals;
}

export function selectModeAvailability(
  state: QoSenseState,
  mode: ExperimentMode,
): { degraded: boolean; detail: string } | null {
  const found = state.system?.modes.find((m) => m.mode === mode);
  return found ? { degraded: found.degraded, detail: found.detail } : null;
}

export function selectIsRunning(state: QoSenseState): boolean {
  return state.experiment?.status === 'RUNNING';
}
