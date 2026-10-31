/**
 * The remaining pages: Live Traffic, AI Intelligence, QoS Controller,
 * Experiments, Performance, Safety and Logs, and Settings.
 */

import { useEffect, useState } from 'react';

import type { MetricSummary } from '@/api/types';
import { apiBaseUrl } from '@/api/client';
import {
  DelayJitterChart,
  JitterArrivalTimeline,
  QueueLossChart,
  ThroughputChart,
} from '@/components/charts';
import { EventTimeline } from '@/components/EventTimeline';
import { FlowTable } from '@/components/FlowTable';
import {
  Badge,
  CheckList,
  DataModeBadge,
  EmptyState,
  KeyValue,
  Notice,
  PageHeader,
  Panel,
} from '@/components/primitives';
import { ExperimentRunner } from '@/panels/ExperimentRunner';
import {
  CongestionPredictorPanel,
  NetworkHealthPanel,
  TrafficClassifierPanel,
  TrafficFlowPanel,
} from '@/panels/observation';
import {
  EnforcementPanel,
  IntentPanel,
  PriorityDecisionCentre,
  SafetyGuardPanel,
  VerificationPanel,
} from '@/panels/decision';
import { CLASS_COLOR, CLASS_LABEL, modelStateTone } from '@/lib/colors';
import { humanise, num, seconds } from '@/lib/format';
import { selectFlowList, selectModels, useQoSenseStore } from '@/store/useQoSenseStore';
import { useShallow } from 'zustand/react/shallow';

// ---------------------------------------------------------------------------

export function LiveTraffic() {
  const flows = useQoSenseStore(useShallow(selectFlowList));
  const classifications = useQoSenseStore((s) => s.classifications);
  const selectedFlowId = useQoSenseStore((s) => s.selectedFlowId);
  const selectFlow = useQoSenseStore((s) => s.selectFlow);
  const featureWindows = useQoSenseStore((s) => s.featureWindows);
  const dataMode = useQoSenseStore((s) => s.experiment?.config.data_mode ?? 'SIMULATION');

  return (
    <>
      <PageHeader
        title="Live traffic"
        description="Every flow the observer currently sees, with the class the controller assigned and the evidence behind it."
        actions={<DataModeBadge mode={dataMode} />}
      />
      <Panel title="Active flows" flush>
        <div style={{ padding: 'var(--gap)' }}>
          <FlowTable
            flows={flows}
            classifications={classifications}
            selectedFlowId={selectedFlowId}
            onSelect={selectFlow}
            showFilters
          />
        </div>
      </Panel>
      <div className="pipeline-grid">
        <TrafficFlowPanel
          flows={flows}
          featureWindows={featureWindows}
          selectedFlowId={selectedFlowId}
        />
        <Panel title="Class legend" hint="Consistent across every page">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {Object.entries(CLASS_LABEL).map(([key, label]) => (
              <div
                key={key}
                style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}
              >
                <span
                  className="legend__swatch"
                  style={{ background: CLASS_COLOR[key as keyof typeof CLASS_COLOR] }}
                />
                <span>{label}</span>
                <span className="faint small" style={{ marginLeft: 'auto' }}>
                  {flows.filter((f) => f.traffic_class === key).length} flows
                </span>
              </div>
            ))}
          </div>
          <p className="small faint" style={{ margin: 0 }}>
            These are broad behaviour classes inferred from flow metadata. QoSense does not claim
            to identify the application behind an encrypted flow.
          </p>
        </Panel>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------

export function AIIntelligence() {
  const models = useQoSenseStore(useShallow(selectModels));
  const flows = useQoSenseStore(useShallow(selectFlowList));
  const classifications = useQoSenseStore((s) => s.classifications);
  const prediction = useQoSenseStore((s) => s.prediction);
  const snapshot = useQoSenseStore((s) => s.snapshot);
  const selectedFlowId = useQoSenseStore((s) => s.selectedFlowId);

  const selectedFlow =
    flows.find((f) => f.flow_id === selectedFlowId) ??
    [...flows].sort((a, b) => b.bytes_per_second - a.bytes_per_second)[0];

  return (
    <>
      <PageHeader
        title="Model intelligence"
        description="The real state of both Light Gradient Boosting Machine models. A model is shown as active only when an artifact is loaded and can run inference."
      />

      <div className="pipeline-grid">
        {models.map((model) => (
          <Panel
            key={model.key}
            title={model.display_name}
            actions={
              <Badge tone={modelStateTone(model.state)} dot>
                {humanise(model.state)}
              </Badge>
            }
          >
            <p className="small muted" style={{ margin: 0 }}>
              {model.purpose}
            </p>
            <KeyValue
              rows={[
                ['Algorithm', model.algorithm],
                ['Target', <span className="small">{model.target}</span>],
                ['Version', model.version],
                ['Artifact', model.artifact_path ?? 'none configured'],
                ['Feature schema', model.feature_schema_version ?? 'not applicable'],
                [
                  'Calibration',
                  model.calibration_available ? 'Available' : 'Not available',
                ],
                ['Inferences run', String(model.inference_count)],
                ['Last inference', model.last_inference_at ?? 'never'],
                ['Scheduled', `${model.implementation_phase} · ${model.round_allocation}`],
              ]}
            />
            <Notice tone={model.state === 'READY' ? 'success' : 'warning'}>{model.detail}</Notice>
          </Panel>
        ))}
      </div>

      <div className="pipeline-grid">
        <TrafficClassifierPanel
          status={models.find((m) => m.key === 'traffic_classifier')}
          classification={selectedFlow ? classifications[selectedFlow.flow_id] : undefined}
          flow={selectedFlow}
        />
        <CongestionPredictorPanel
          status={models.find((m) => m.key === 'congestion_predictor')}
          prediction={prediction}
          measured={snapshot}
        />
      </div>

      <Panel title="What the classifier produced for each flow">
        {Object.keys(classifications).length === 0 ? (
          <EmptyState title="No classification has run yet." />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Flow</th>
                  <th scope="col">Class</th>
                  <th scope="col">Produced by</th>
                  <th scope="col">Decision</th>
                  <th scope="col">Reason</th>
                  <th scope="col">Calibrated confidence</th>
                  <th scope="col" className="num">
                    Inference
                  </th>
                </tr>
              </thead>
              <tbody>
                {Object.values(classifications).map((result) => (
                  <tr key={result.flow_id}>
                    <td className="mono">{result.flow_id}</td>
                    <td style={{ color: CLASS_COLOR[result.predicted_class] }}>
                      {CLASS_LABEL[result.predicted_class]}
                    </td>
                    <td className="small">
                      {result.source === 'MODEL_1'
                        ? 'Model 1'
                        : result.source === 'DETERMINISTIC_RULES'
                          ? 'Rule set'
                          : 'Safe fallback'}
                    </td>
                    <td className="small">{humanise(result.decision)}</td>
                    <td className="small faint">{result.reason}</td>
                    <td className="small">
                      {result.calibrated_confidence === null ? (
                        <span className="faint">not produced</span>
                      ) : (
                        `${(result.calibrated_confidence * 100).toFixed(0)}%`
                      )}
                    </td>
                    <td className="num small">
                      {result.inference_ms === null ? '—' : `${result.inference_ms.toFixed(3)} ms`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small faint" style={{ margin: 0 }}>
          A gradient-boosted probability is not automatically a calibrated confidence, and a rule
          set produces no probability at all. Neither is invented to fill the column.
        </p>
      </Panel>
    </>
  );
}

// ---------------------------------------------------------------------------

export function QoSController() {
  const snapshot = useQoSenseStore((s) => s.snapshot);
  const proposal = useQoSenseStore((s) => s.proposal);
  const inputs = useQoSenseStore((s) => s.evaluationInputs);
  const enforcement = useQoSenseStore((s) => s.lastEnforcement);
  const verification = useQoSenseStore((s) => s.lastVerification);
  const prediction = useQoSenseStore((s) => s.prediction);
  const intents = useQoSenseStore((s) => s.intents);
  const topology = useQoSenseStore((s) => s.topology);
  const busy = useQoSenseStore((s) => s.busy);
  const createIntent = useQoSenseStore((s) => s.createIntent);
  const cancelIntent = useQoSenseStore((s) => s.cancelIntent);
  const refreshPolicy = useQoSenseStore((s) => s.refreshPolicy);
  const history = useQoSenseStore((s) => s.policyHistory);

  useEffect(() => {
    void refreshPolicy();
  }, [refreshPolicy]);

  const devices =
    topology?.devices
      .filter((d) => d.device_id !== 'gateway' && d.device_id !== 'internet')
      .map((d) => ({ device_id: d.device_id, label: d.label })) ?? [];

  const installedRates = enforcement?.read_back?.observed_class_rates_mbps ?? {};

  return (
    <>
      <PageHeader
        title="QoS controller"
        description="Capacity, congestion, the candidate policy and what actually reached the data plane."
      />

      <div className="pipeline-grid">
        <Panel title="Usable capacity estimate" hint="Deterministic, not a model">
          <KeyValue
            rows={[
              ['Estimate', `${num(snapshot?.usable_capacity_mbps, 2)} Mbps`],
              ['Quality', humanise(snapshot?.capacity_quality ?? 'NO_DATA')],
              ['Measured download', `${num(snapshot?.download_mbps, 2)} Mbps`],
              ['Measured upload', `${num(snapshot?.upload_mbps, 2)} Mbps`],
            ]}
          />
          <p className="small faint" style={{ margin: 0 }}>
            The estimator uses observed delivery rate and delay evidence only. It never reads the
            emulated bottleneck setting, which is the hidden quantity it has to discover.
          </p>
        </Panel>

        <CongestionPredictorPanel
          status={useQoSenseStore
            .getState()
            .system?.models.find((m) => m.key === 'congestion_predictor')}
          prediction={prediction}
          measured={snapshot}
        />

        <IntentPanel
          intents={intents}
          devices={devices}
          busy={busy}
          onCreate={(targetId, duration) =>
            void createIntent({
              targetId,
              intentType: 'PRIORITIZE_INTERACTIVE',
              durationSeconds: duration,
              reason: 'Requested from the QoS controller page',
            })
          }
          onCancel={(id) => void cancelIntent(id)}
        />
      </div>

      <PriorityDecisionCentre inputs={inputs} proposal={proposal} />

      <div className="pipeline-grid">
        <SafetyGuardPanel proposal={proposal} />
        <EnforcementPanel
          enforcement={enforcement}
          proposal={proposal}
          installedRates={installedRates}
        />
        <VerificationPanel verification={verification} />
      </div>

      <Panel title="Policy history" flush>
        {history.length === 0 ? (
          <EmptyState title="No policy has been proposed yet." />
        ) : (
          <div className="table-wrap" style={{ maxHeight: 320 }}>
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Policy</th>
                  <th scope="col">Profile</th>
                  <th scope="col">Guard</th>
                  <th scope="col">Enforcement</th>
                  <th scope="col">Verification</th>
                  <th scope="col">Reasons</th>
                </tr>
              </thead>
              <tbody>
                {[...history].reverse().map((entry) => (
                  <tr key={entry.proposal.policy_id}>
                    <td className="mono">{entry.proposal.policy_id}</td>
                    <td>{humanise(entry.proposal.profile)}</td>
                    <td
                      style={{
                        color:
                          entry.proposal.status === 'ACCEPTED'
                            ? 'var(--success)'
                            : 'var(--critical)',
                      }}
                    >
                      {humanise(entry.proposal.status)}
                    </td>
                    <td className="small">
                      {entry.enforcement ? humanise(entry.enforcement.status) : '—'}
                    </td>
                    <td className="small">
                      {entry.verification ? humanise(entry.verification.outcome) : '—'}
                    </td>
                    <td className="small faint">
                      {entry.proposal.reason_codes.slice(0, 3).map(humanise).join(', ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}

// ---------------------------------------------------------------------------

export function Experiments() {
  const experiments = useQoSenseStore((s) => s.experiments);
  const report = useQoSenseStore((s) => s.report);
  const experiment = useQoSenseStore((s) => s.experiment);
  const refreshReport = useQoSenseStore((s) => s.refreshReport);

  return (
    <>
      <PageHeader
        title="Experiments"
        description="Configure a predefined scenario, choose a comparison mode and run it. Every run records its scenario, mode, seed and parameters so it can be repeated exactly."
        actions={
          experiment && (
            <button type="button" className="btn" onClick={() => void refreshReport()}>
              Refresh report
            </button>
          )
        }
      />

      <div className="overview-grid" style={{ minHeight: 0 }}>
        <ExperimentRunner />
        <Panel title="Runs in this session" flush>
          {experiments.length === 0 ? (
            <EmptyState title="No experiment has been created yet." />
          ) : (
            <div className="table-wrap" style={{ maxHeight: 360 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">Run</th>
                    <th scope="col">Scenario</th>
                    <th scope="col">Mode</th>
                    <th scope="col" className="num">
                      Seed
                    </th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {[...experiments].reverse().map((item) => (
                    <tr key={item.config.experiment_id}>
                      <td className="mono">{item.config.experiment_id}</td>
                      <td className="small">{item.config.scenario_name}</td>
                      <td className="small">
                        {humanise(item.config.mode)}
                        {item.mode_availability.degraded && (
                          <span style={{ color: 'var(--warning)' }}> · degraded</span>
                        )}
                      </td>
                      <td className="num">{item.config.seed}</td>
                      <td className="small">{humanise(item.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      {report && <ReportPanel />}
    </>
  );
}

function ReportPanel() {
  const report = useQoSenseStore((s) => s.report);
  if (!report) return null;

  const exportReport = () => {
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${report.experiment_id}-report.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Panel
      title={`Report · ${report.experiment_id}`}
      actions={
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <DataModeBadge mode={report.data_mode} />
          <button type="button" className="btn" style={{ padding: '3px 9px' }} onClick={exportReport}>
            Export JSON
          </button>
        </div>
      }
    >
      <KeyValue
        rows={[
          ['Scenario', report.config.scenario_name],
          ['Mode', humanise(report.config.mode)],
          ['Seed', String(report.config.seed)],
          ['Observed duration', seconds(report.duration_observed_seconds)],
          ['Samples', String(report.sample_count)],
          ['Policy changes', String(report.policy_changes)],
          ['Safety rejections', String(report.safety_rejections)],
          ['Rollbacks', String(report.rollbacks)],
          ['Zero-progress windows for bulk', String(report.zero_progress_windows)],
          [
            'Bulk transferred',
            report.bulk_transferred_megabytes === null
              ? '—'
              : `${report.bulk_transferred_megabytes.toFixed(1)} MB`,
          ],
          [
            "Jain's fairness index",
            report.jain_fairness_index === null
              ? 'Not computed'
              : report.jain_fairness_index.toFixed(4),
          ],
        ]}
      />
      <p className="small faint" style={{ margin: 0 }}>
        {report.fairness_basis}
      </p>

      <SummaryTable summaries={report.summaries} />

      <Notice tone="warning">
        <strong>Limitations</strong>
        <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
          {report.limitations.map((limitation) => (
            <li key={limitation}>{limitation}</li>
          ))}
        </ul>
      </Notice>
    </Panel>
  );
}

function SummaryTable({ summaries }: { summaries: MetricSummary[] }) {
  if (summaries.length === 0) return <EmptyState title="No metric samples recorded." />;
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col">Unit</th>
            <th scope="col" className="num">
              Samples
            </th>
            <th scope="col" className="num">
              Median
            </th>
            <th scope="col" className="num">
              25th
            </th>
            <th scope="col" className="num">
              75th
            </th>
            <th scope="col" className="num">
              Min
            </th>
            <th scope="col" className="num">
              Max
            </th>
          </tr>
        </thead>
        <tbody>
          {summaries.map((summary) => (
            <tr key={summary.metric}>
              <td>{summary.metric.replace(/_/g, ' ')}</td>
              <td className="small faint">{summary.unit}</td>
              <td className="num">{summary.samples}</td>
              <td className="num">{num(summary.median, 2)}</td>
              <td className="num">{num(summary.p25, 2)}</td>
              <td className="num">{num(summary.p75, 2)}</td>
              <td className="num">{num(summary.minimum, 2)}</td>
              <td className="num">{num(summary.maximum, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------

export function Performance() {
  const series = useQoSenseStore((s) => s.series);
  const snapshot = useQoSenseStore((s) => s.snapshot);
  const report = useQoSenseStore((s) => s.report);
  const refreshReport = useQoSenseStore((s) => s.refreshReport);

  useEffect(() => {
    void refreshReport();
  }, [refreshReport]);

  return (
    <>
      <PageHeader
        title="Performance"
        description="Measured delay, jitter, loss, throughput and queue depth, with each metric defined before it is plotted."
      />

      <div className="pipeline-grid">
        <Panel title="Throughput and capacity estimate">
          <ThroughputChart series={series} height={180} />
        </Panel>
        <Panel title="Round-trip time, added delay and jitter">
          <DelayJitterChart series={series} targetMs={30} height={180} />
        </Panel>
        <Panel title="Queue occupancy and packet loss">
          <QueueLossChart series={series} height={180} />
        </Panel>
        <Panel title="Packet arrival spacing">
          <JitterArrivalTimeline
            jitterMs={snapshot?.jitter_ms ?? null}
            rttMs={snapshot?.rtt_ms ?? null}
            height={56}
          />
        </Panel>
      </div>

      <Panel title="Metric definitions used here">
        <CheckList
          checks={[
            {
              name: 'Round-trip time',
              passed: true,
              detail:
                'Probe round-trip time. Not one-way application latency and not a call quality score.',
            },
            {
              name: 'Added delay under load',
              passed: true,
              detail:
                'Loaded round-trip time minus the idle baseline round-trip time, measured by the same probe method.',
            },
            {
              name: 'Jitter',
              passed: true,
              detail:
                'Packet delay variation: the exponentially weighted moving average of the absolute difference between consecutive probe round-trip times. One definition is used everywhere.',
            },
            {
              name: 'Packet loss',
              passed: true,
              detail:
                'Dropped packets divided by offered packets over the sampling interval, as a percentage.',
            },
            {
              name: 'Queue depth',
              passed: true,
              detail:
                'Simulator queue state in this build, clearly labelled. It is read from the Linux queue discipline only once a laboratory agent is connected.',
            },
            {
              name: "Jain's fairness index",
              passed: true,
              detail:
                'Computed across comparable flows only, meaning flows in the same class with the same service entitlement, over bytes delivered during the run.',
            },
          ]}
        />
      </Panel>

      {report && <SummaryTable summaries={report.summaries} />}
    </>
  );
}

// ---------------------------------------------------------------------------

export function SafetyLogs() {
  const events = useQoSenseStore((s) => s.events);
  const proposal = useQoSenseStore((s) => s.proposal);
  const system = useQoSenseStore((s) => s.system);
  const history = useQoSenseStore((s) => s.policyHistory);

  const rejections = history.filter((entry) => entry.proposal.status === 'REJECTED');
  const rollbacks = events.filter(
    (e) => e.type === 'rollback.started' || e.type === 'rollback.completed',
  );

  return (
    <>
      <PageHeader
        title="Safety and logs"
        description="The full chronological trace: safety rejections, enforcement transactions, rollbacks, watchdog state and errors."
      />

      <div className="pipeline-grid">
        <Panel title="Watchdog">
          <KeyValue
            rows={[
              ['Last heartbeat', system?.watchdog_last_heartbeat ?? 'never'],
              ['Alerts raised', String(system?.watchdog_alerts ?? 0)],
            ]}
          />
          <p className="small faint" style={{ margin: 0 }}>
            The watchdog monitors the controller independently. Its only authority is to activate
            the known-safe profile; it does not depend on the controller reporting its own failure.
          </p>
        </Panel>

        <Panel title="Laboratory agent boundary">
          <KeyValue
            rows={[
              ['Configured', system?.lab_agent.configured ? 'Yes' : 'No'],
              ['Reachable', system?.lab_agent.reachable ? 'Yes' : 'No'],
              [
                'Live enforcement',
                system?.lab_agent.live_enforcement_enabled ? 'Enabled' : 'Disabled',
              ],
            ]}
          />
          <Notice tone="warning">{system?.lab_agent.detail}</Notice>
        </Panel>

        <SafetyGuardPanel proposal={proposal} />
      </div>

      <div className="pipeline-grid">
        <Panel title={`Safety rejections (${rejections.length})`} flush>
          {rejections.length === 0 ? (
            <EmptyState
              title="No proposal has been rejected."
              hint="A rejection is always logged and shown here when it happens."
            />
          ) : (
            <div className="table-wrap" style={{ maxHeight: 260 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">Policy</th>
                    <th scope="col">Profile</th>
                    <th scope="col">Failed checks</th>
                  </tr>
                </thead>
                <tbody>
                  {[...rejections].reverse().map((entry) => (
                    <tr key={entry.proposal.policy_id}>
                      <td className="mono">{entry.proposal.policy_id}</td>
                      <td>{humanise(entry.proposal.profile)}</td>
                      <td className="small" style={{ color: 'var(--critical)' }}>
                        {entry.proposal.safety?.rejection_reasons.map(humanise).join(', ')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel title={`Rollbacks (${rollbacks.length})`} flush style={{ maxHeight: 300 }}>
          <EventTimeline
            events={events}
            types={['rollback.started', 'rollback.completed', 'watchdog.alert']}
            emptyHint="A rollback is recorded only when the backend starts one."
          />
        </Panel>
      </div>

      <Panel title="Full event trace" flush style={{ maxHeight: 420 }}>
        <EventTimeline events={events} limit={300} />
      </Panel>
    </>
  );
}

// ---------------------------------------------------------------------------

export function SettingsPage() {
  const system = useQoSenseStore((s) => s.system);
  const prefer2D = useQoSenseStore((s) => s.prefer2D);
  const setPrefer2D = useQoSenseStore((s) => s.setPrefer2D);
  const webglFailed = useQoSenseStore((s) => s.webglFailed);
  const experiment = useQoSenseStore((s) => s.experiment);
  const [health, setHealth] = useState<string>('checking…');

  useEffect(() => {
    let cancelled = false;
    void fetch(`${apiBaseUrl}/api/v1/health`)
      .then((r) => (r.ok ? 'reachable' : `error ${r.status}`))
      .catch(() => 'unreachable')
      .then((result) => {
        if (!cancelled) setHealth(result);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <PageHeader
        title="Settings"
        description="Connection, data mode and display preferences. Privileged actions are not available from this interface."
      />

      <div className="pipeline-grid">
        <Panel title="Connection">
          <KeyValue
            rows={[
              ['API base URL', apiBaseUrl],
              ['Health', health],
              ['Build', system?.build_version ?? '—'],
              ['Event subscribers', String(system?.subscriber_count ?? 0)],
            ]}
          />
        </Panel>

        <Panel title="Data mode">
          <KeyValue
            rows={[
              ['Current', system?.data_mode ?? 'SIMULATION'],
              ['Supported', (system?.supported_data_modes ?? []).join(', ') || '—'],
            ]}
          />
          <Notice tone="warning">
            Only SIMULATION is available in this build. LIVE requires a connected Ubuntu
            laboratory agent and REPLAY requires recorded runs; neither is implemented yet, and
            the interface will not offer them until they are.
          </Notice>
        </Panel>

        <Panel title="Display">
          <label
            className="field"
            style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}
          >
            <input
              type="checkbox"
              checked={prefer2D}
              style={{ width: 'auto' }}
              onChange={(e) => setPrefer2D(e.target.checked)}
            />
            <span>Use the two-dimensional topology view</span>
          </label>
          <p className="small faint" style={{ margin: 0 }}>
            The two-dimensional view shows the same state as the scene. It is selected
            automatically when WebGL is unavailable or the renderer fails.
            {webglFailed && ' WebGL is unavailable in this browser.'}
          </p>
          <p className="small faint" style={{ margin: 0 }}>
            Scene animation already stops when this tab is hidden and when the system asks for
            reduced motion. Neither affects measurement: the simulator advances on the server.
          </p>
        </Panel>

        <Panel title="Reproducibility of the current run">
          {experiment ? (
            <KeyValue
              rows={[
                ['Experiment', experiment.config.experiment_id],
                ['Scenario', experiment.config.scenario_id],
                ['Mode', humanise(experiment.config.mode)],
                ['Seed', String(experiment.config.seed)],
                ['Tick', `${experiment.config.tick_seconds} s`],
                ['Policy weights', experiment.config.weights_version],
                ['Feature schema', experiment.config.feature_schema_version],
                [
                  'Model versions',
                  <span className="small">
                    {Object.entries(experiment.config.model_versions)
                      .map(([key, value]) => `${key}: ${value}`)
                      .join(' · ')}
                  </span>,
                ],
              ]}
            />
          ) : (
            <EmptyState title="No experiment is loaded." />
          )}
        </Panel>
      </div>

      <Panel title="System status">
        <NetworkHealthPanel snapshot={useQoSenseStore.getState().snapshot} />
      </Panel>
    </>
  );
}
