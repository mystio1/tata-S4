/**
 * Whole-application smoke test.
 *
 * Mounts the real App against a stubbed API and event stream and walks every
 * page. This is what catches a composition error that unit tests on individual
 * panels would miss.
 *
 * The three-dimensional canvas is not exercised here: jsdom has no WebGL, so
 * the application's own two-dimensional fallback is used, which is exactly the
 * path a viewer without WebGL gets.
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { useQoSenseStore } from '@/store/useQoSenseStore';
import * as fx from './fixtures';

const systemStatus = {
  build_version: '0.1.0-phase1',
  data_mode: 'SIMULATION',
  supported_data_modes: ['SIMULATION'],
  controller_running: false,
  models: [
    {
      key: 'traffic_classifier',
      model_name: 'traffic_classifier',
      display_name: 'Model 1 - Traffic classifier',
      purpose: 'Predicts a broad traffic behaviour class.',
      algorithm: 'LightGBM multiclass',
      target: 'INTERACTIVE, BULK',
      state: 'NOT_CONFIGURED',
      version: 'not-trained',
      artifact_path: null,
      feature_schema_version: 'flow-features-v1',
      calibration_available: false,
      last_inference_at: null,
      inference_count: 0,
      implementation_phase: 'Phase 4',
      round_allocation: 'Round 1',
      detail: 'No artifact configured, so this model is not producing any prediction.',
    },
    {
      key: 'congestion_predictor',
      model_name: 'congestion_predictor',
      display_name: 'Model 2 - Congestion-risk predictor',
      purpose: 'Predicts a congestion event in a future horizon.',
      algorithm: 'LightGBM binary',
      target: 'Congestion event',
      state: 'NOT_CONFIGURED',
      version: 'not-trained',
      artifact_path: null,
      feature_schema_version: null,
      calibration_available: false,
      last_inference_at: null,
      inference_count: 0,
      implementation_phase: 'Phase 5',
      round_allocation: 'Round 2',
      detail: 'Scheduled for Round 2, so this model is not producing any prediction.',
    },
  ],
  lab_agent: {
    configured: false,
    reachable: false,
    live_enforcement_enabled: false,
    detail: 'No Ubuntu laboratory agent is connected.',
  },
  modes: [
    { mode: 'NO_QOS', available: true, degraded: false, requires: [], detail: 'Reference.' },
    {
      mode: 'TRADITIONAL_ADAPTIVE',
      available: true,
      degraded: false,
      requires: [],
      detail: 'Runs end to end.',
    },
    {
      mode: 'CLASSIFIER_ONLY',
      available: true,
      degraded: true,
      requires: ['Model 1'],
      detail: 'Model 1 is not trained in this build.',
    },
    {
      mode: 'FULL_QOSENSE',
      available: true,
      degraded: true,
      requires: ['Model 1', 'Model 2'],
      detail: 'Model 1 and Model 2 are not trained in this build.',
    },
  ],
  active_experiment_id: null,
  experiment: null,
  watchdog_last_heartbeat: null,
  watchdog_alerts: 0,
  subscriber_count: 0,
  server_time: '2026-01-01T00:00:00Z',
};

const scenarios = [
  {
    scenario_id: 'iso_during_call',
    name: 'Disc image download during a work call',
    summary: 'Primary demonstrator scenario.',
    is_laboratory_emulation: true,
    default_duration_seconds: 120,
    default_download_mbps: 100,
    default_upload_mbps: 20,
    default_base_rtt_ms: 18,
    queue_limit_packets: 900,
    parameters: [
      {
        name: 'bottleneck_download_mbps',
        label: 'Bottleneck download rate',
        unit: 'Mbps',
        minimum: 5,
        maximum: 500,
        default: 100,
        description: 'Emulated downstream bottleneck.',
      },
    ],
  },
];

class StubSocket {
  static instances: StubSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(readonly url: string) {
    StubSocket.instances.push(this);
    queueMicrotask(() => this.onopen?.());
  }

  send() {}
  close() {}
}

function stubFetch(url: string) {
  const body = (payload: unknown) =>
    Promise.resolve(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  if (url.includes('/system/status')) return body(systemStatus);
  if (url.includes('/scenarios')) return body(scenarios);
  if (url.includes('/experiments') && !url.includes('/report')) return body([]);
  if (url.includes('/health')) return body({ status: 'ok', build_version: '0.1.0-phase1' });
  return body([]);
}

describe('application shell', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => stubFetch(String(input))));
    vi.stubGlobal('WebSocket', StubSocket as unknown as typeof WebSocket);
    StubSocket.instances = [];
    useQoSenseStore.setState({
      bootstrapped: false,
      bootstrapError: null,
      page: 'overview',
      prefer2D: true,
      snapshot: null,
      series: [],
      flows: {},
      classifications: {},
      events: [],
      experiment: null,
      experiments: [],
      system: null,
      scenarios: [],
      topology: null,
      report: null,
      policyHistory: [],
      intents: [],
      proposal: null,
      lastEnforcement: null,
      lastVerification: null,
      prediction: null,
      evaluationInputs: null,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('boots and shows the simulation badge', async () => {
    render(<App />);
    await waitFor(() => expect(screen.getAllByText('SIMULATION').length).toBeGreaterThan(0));
    expect(screen.getByText('QoSense')).toBeInTheDocument();
  });

  it('shows no measured values before any experiment runs', async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText('Download')).toBeInTheDocument());
    // Every top metric tile should read as unmeasured, not as zero.
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThan(0);
  });

  it('renders every page without crashing', async () => {
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByText('Network Overview')).toBeInTheDocument());

    for (const label of [
      'Live Traffic',
      'AI Intelligence',
      'QoS Controller',
      'Experiments',
      'Performance',
      'Safety & Logs',
      'Settings',
      'Network Overview',
    ]) {
      await user.click(screen.getByRole('button', { name: new RegExp(label, 'i') }));
      await waitFor(() =>
        expect(screen.getByRole('button', { name: new RegExp(label, 'i') })).toHaveAttribute(
          'aria-current',
          'page',
        ),
      );
    }
  });

  it('marks the two model-dependent modes as degraded', async () => {
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByText('AI Intelligence')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /AI Intelligence/i }));

    // Each model appears both as its own card and inside the pipeline panel.
    await waitFor(() =>
      expect(screen.getAllByText('Model 1 - Traffic classifier').length).toBeGreaterThan(0),
    );
    expect(
      screen.getAllByText('Model 2 - Congestion-risk predictor').length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText('Not configured').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText(/not producing any prediction/).length).toBeGreaterThan(0);
  });

  it('updates the interface from a streamed metrics snapshot', async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText('Download')).toBeInTheDocument());

    useQoSenseStore.getState().ingest(fx.event('metrics.snapshot', fx.snapshot()));

    await waitFor(() => expect(screen.getByText('68.4')).toBeInTheDocument());
    // Measured congestion is surfaced in the metric strip and the health panel.
    expect(screen.getAllByText('Congested').length).toBeGreaterThan(0);
    // The predicted-risk tile must stay explicitly unavailable.
    expect(screen.getAllByText('Unavailable').length).toBeGreaterThan(0);
  });

  it('never offers a live data mode while no agent is connected', async () => {
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByText('Settings')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /Settings/i }));

    await waitFor(() => expect(screen.getByText('API base URL')).toBeInTheDocument());
    expect(screen.getByText(/Only SIMULATION is available in this build/)).toBeInTheDocument();
  });

  it('reports a backend it cannot reach instead of showing placeholder data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('connection refused'))),
    );
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/Cannot reach the QoSense API/)).toBeInTheDocument(),
    );
  });
});
