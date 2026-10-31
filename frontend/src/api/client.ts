/** Typed REST client. Every call surfaces a readable error for the interface. */

import type {
  EventPage,
  EventType,
  ExperimentCreateRequest,
  ExperimentReport,
  ExperimentState,
  FlowState,
  IntentLease,
  IntentType,
  MetricsSnapshot,
  ModelStatus,
  PolicyHistoryEntry,
  QoSControllerView,
  ScenarioDefinition,
  SystemStatus,
  Topology,
} from './types';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const WS_BASE = import.meta.env.VITE_WS_BASE_URL ?? API_BASE.replace(/^http/, 'ws');

export const apiBaseUrl = API_BASE;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
  } catch (cause) {
    throw new ApiError(
      `Cannot reach the QoSense API at ${API_BASE}. Is the backend running?`,
      0,
      cause,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const body = text ? safeParse(text) : undefined;

  if (!response.ok) {
    throw new ApiError(describeError(body, response.status), response.status, body);
  }
  return body as T;
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function describeError(body: unknown, status: number): string {
  if (typeof body === 'string' && body) return body;
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (item && typeof item === 'object' && 'msg' in item) {
            const loc = 'loc' in item && Array.isArray(item.loc) ? item.loc.join('.') : '';
            return loc ? `${loc}: ${String(item.msg)}` : String(item.msg);
          }
          return String(item);
        })
        .join('; ');
    }
  }
  return `Request failed with status ${status}.`;
}

function withExperiment(path: string, experimentId?: string | null): string {
  if (!experimentId) return path;
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}experiment_id=${encodeURIComponent(experimentId)}`;
}

export const api = {
  health: () => request<{ status: string; build_version: string }>('/api/v1/health'),

  systemStatus: () => request<SystemStatus>('/api/v1/system/status'),

  scenarios: () => request<ScenarioDefinition[]>('/api/v1/scenarios'),

  models: () => request<ModelStatus[]>('/api/v1/models/status'),

  topology: (experimentId?: string | null) =>
    request<Topology>(withExperiment('/api/v1/topology', experimentId)),

  currentMetrics: (experimentId?: string | null) =>
    request<MetricsSnapshot | null>(withExperiment('/api/v1/metrics/current', experimentId)),

  flows: (experimentId?: string | null) =>
    request<FlowState[]>(withExperiment('/api/v1/flows', experimentId)),

  events: (options: {
    experimentId?: string | null;
    limit?: number;
    types?: EventType[];
  } = {}) => {
    const params = new URLSearchParams();
    if (options.experimentId) params.set('experiment_id', options.experimentId);
    if (options.limit) params.set('limit', String(options.limit));
    options.types?.forEach((type) => params.append('event_type', type));
    return request<EventPage>(`/api/v1/events?${params.toString()}`);
  },

  currentPolicy: (experimentId?: string | null) =>
    request<QoSControllerView>(withExperiment('/api/v1/policies/current', experimentId)),

  policyHistory: (experimentId?: string | null) =>
    request<PolicyHistoryEntry[]>(withExperiment('/api/v1/policies/history', experimentId)),

  listExperiments: () => request<ExperimentState[]>('/api/v1/experiments'),

  createExperiment: (body: ExperimentCreateRequest) =>
    request<ExperimentState>('/api/v1/experiments', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  startExperiment: (id: string) =>
    request<ExperimentState>(`/api/v1/experiments/${id}/start`, { method: 'POST' }),

  pauseExperiment: (id: string) =>
    request<ExperimentState>(`/api/v1/experiments/${id}/pause`, { method: 'POST' }),

  resumeExperiment: (id: string) =>
    request<ExperimentState>(`/api/v1/experiments/${id}/resume`, { method: 'POST' }),

  stopExperiment: (id: string) =>
    request<ExperimentState>(`/api/v1/experiments/${id}/stop`, { method: 'POST' }),

  resetExperiment: (id: string) =>
    request<ExperimentState>(`/api/v1/experiments/${id}/reset`, { method: 'POST' }),

  report: (id: string) => request<ExperimentReport>(`/api/v1/experiments/${id}/report`),

  createIntent: (body: {
    target_type: string;
    target_id: string;
    intent_type: IntentType;
    duration_seconds: number;
    reason: string;
    experiment_id: string | null;
  }) => request<IntentLease>('/api/v1/intents', { method: 'POST', body: JSON.stringify(body) }),

  cancelIntent: (intentId: string, experimentId?: string | null) =>
    request<IntentLease>(withExperiment(`/api/v1/intents/${intentId}`, experimentId), {
      method: 'DELETE',
    }),
};

export function experimentSocketUrl(experimentId: string): string {
  return `${WS_BASE}/api/v1/ws/experiments/${encodeURIComponent(experimentId)}`;
}
