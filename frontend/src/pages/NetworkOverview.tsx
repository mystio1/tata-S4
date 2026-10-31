/**
 * Network Overview: the three-dimensional topology, the health column and the
 * seven engineering panels that trace one decision from observation through
 * to verification.
 */

import { useMemo } from 'react';

import { DelayJitterChart, ThroughputChart } from '@/components/charts';
import { EventTimeline } from '@/components/EventTimeline';
import { Badge, Panel } from '@/components/primitives';
import {
  CongestionPredictorPanel,
  NetworkHealthPanel,
  TrafficClassifierPanel,
  TrafficFlowPanel,
} from '@/panels/observation';
import {
  EnforcementPanel,
  PriorityDecisionCentre,
  SafetyGuardPanel,
  VerificationPanel,
} from '@/panels/decision';
import { ExperimentRunner } from '@/panels/ExperimentRunner';
import { NetworkTopology3D } from '@/scene/NetworkTopology3D';
import { selectFlowList, selectModels, useQoSenseStore } from '@/store/useQoSenseStore';
import { useShallow } from 'zustand/react/shallow';

/** How long a scene cue stays visible after the event that caused it. */
const CUE_LIFETIME_MS = 1600;

export function NetworkOverview() {
  const topology = useQoSenseStore((s) => s.topology);
  const flows = useQoSenseStore(useShallow(selectFlowList));
  const snapshot = useQoSenseStore((s) => s.snapshot);
  const series = useQoSenseStore((s) => s.series);
  const events = useQoSenseStore((s) => s.events);
  const cues = useQoSenseStore((s) => s.cues);
  const classifications = useQoSenseStore((s) => s.classifications);
  const featureWindows = useQoSenseStore((s) => s.featureWindows);
  const models = useQoSenseStore(useShallow(selectModels));
  const prediction = useQoSenseStore((s) => s.prediction);
  const evaluationInputs = useQoSenseStore((s) => s.evaluationInputs);
  const proposal = useQoSenseStore((s) => s.proposal);
  const enforcement = useQoSenseStore((s) => s.lastEnforcement);
  const verification = useQoSenseStore((s) => s.lastVerification);
  const selectedDeviceId = useQoSenseStore((s) => s.selectedDeviceId);
  const selectedFlowId = useQoSenseStore((s) => s.selectedFlowId);
  const selectDevice = useQoSenseStore((s) => s.selectDevice);
  const selectFlow = useQoSenseStore((s) => s.selectFlow);
  const prefer2D = useQoSenseStore((s) => s.prefer2D);
  const setPrefer2D = useQoSenseStore((s) => s.setPrefer2D);
  const reportWebglFailure = useQoSenseStore((s) => s.reportWebglFailure);

  const now = Date.now();
  const recentCues = cues.filter((cue) => now - cue.at < CUE_LIFETIME_MS);
  const highlightedFlowIds = useMemo(
    () =>
      new Set(
        recentCues
          .filter((cue) => cue.kind === 'classified' && cue.flowId)
          .map((cue) => cue.flowId as string),
      ),
    [recentCues],
  );
  const policyPulse = recentCues.some((cue) => cue.kind === 'policy');

  const shapingActive = Boolean(
    enforcement?.read_back && Object.keys(enforcement.read_back.observed_class_rates_mbps).length,
  );

  const selectedFlow =
    flows.find((f) => f.flow_id === selectedFlowId) ??
    (selectedDeviceId ? flows.find((f) => f.device_id === selectedDeviceId) : undefined) ??
    [...flows].sort((a, b) => b.bytes_per_second - a.bytes_per_second)[0];

  const installedRates = enforcement?.read_back?.observed_class_rates_mbps ?? {};

  return (
    <>
      <div className="overview-grid">
        <Panel
          title="Live three-dimensional network topology"
          flush
          actions={
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <Badge tone={shapingActive ? 'success' : 'muted'} dot>
                {shapingActive ? 'Shaping installed' : 'Unshaped'}
              </Badge>
              <button
                type="button"
                className="btn"
                style={{ padding: '3px 9px' }}
                onClick={() => setPrefer2D(!prefer2D)}
                aria-pressed={prefer2D}
              >
                {prefer2D ? 'Use 3D view' : 'Use 2D view'}
              </button>
            </div>
          }
          style={{ minHeight: 480 }}
        >
          <NetworkTopology3D
            topology={topology}
            flows={flows}
            snapshot={snapshot}
            shapingActive={shapingActive}
            highlightedFlowIds={highlightedFlowIds}
            policyPulse={policyPulse}
            selectedDeviceId={selectedDeviceId}
            onSelectDevice={selectDevice}
            prefer2D={prefer2D}
            onWebglFailure={reportWebglFailure}
          />
        </Panel>

        <div className="overview-grid__side">
          <NetworkHealthPanel snapshot={snapshot} />
          <Panel title="Live throughput" hint="Measured delivered rate">
            <ThroughputChart series={series} height={132} />
          </Panel>
          <Panel title="Latency and jitter" hint="Probe measurements">
            <DelayJitterChart series={series} targetMs={30} height={132} />
          </Panel>
          <Panel title="Recent events" flush style={{ height: 230, flex: 'none' }}>
            <EventTimeline events={events} limit={40} />
          </Panel>
        </div>
      </div>

      {selectedDeviceId && (
        <Panel
          title={`Selected device: ${
            topology?.devices.find((d) => d.device_id === selectedDeviceId)?.label ??
            selectedDeviceId
          }`}
          actions={
            <button
              type="button"
              className="btn"
              style={{ padding: '3px 9px' }}
              onClick={() => selectDevice(null)}
            >
              Clear selection
            </button>
          }
        >
          <DeviceDetail deviceId={selectedDeviceId} />
        </Panel>
      )}

      <div className="pipeline-grid">
        <TrafficFlowPanel
          flows={flows}
          featureWindows={featureWindows}
          selectedFlowId={selectedFlow?.flow_id ?? null}
        />
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
        <PriorityDecisionCentre inputs={evaluationInputs} proposal={proposal} />
        <SafetyGuardPanel proposal={proposal} />
        <EnforcementPanel
          enforcement={enforcement}
          proposal={proposal}
          installedRates={installedRates}
        />
        <VerificationPanel verification={verification} />
        <ExperimentRunner compact />
      </div>

      <button
        type="button"
        className="sr-only"
        onClick={() => selectFlow(null)}
        aria-hidden="true"
        tabIndex={-1}
      >
        Clear flow selection
      </button>
    </>
  );
}

function DeviceDetail({ deviceId }: { deviceId: string }) {
  const flows = useQoSenseStore(useShallow(selectFlowList)).filter((f) => f.device_id === deviceId);
  const classifications = useQoSenseStore((s) => s.classifications);

  if (flows.length === 0) {
    return <span className="faint small">This device has no active flow right now.</span>;
  }

  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th scope="col">Flow</th>
            <th scope="col">Direction</th>
            <th scope="col">Class</th>
            <th scope="col">Source</th>
            <th scope="col" className="num">
              Mbps
            </th>
            <th scope="col" className="num">
              Packets/s
            </th>
            <th scope="col" className="num">
              Flow delay
            </th>
            <th scope="col" className="num">
              Served
            </th>
          </tr>
        </thead>
        <tbody>
          {flows.map((flow) => (
            <tr key={flow.flow_id}>
              <td className="mono">{flow.flow_id}</td>
              <td>{flow.direction === 'DOWNLOAD' ? 'Download' : 'Upload'}</td>
              <td>{flow.traffic_class}</td>
              <td className="small">
                {classifications[flow.flow_id]?.reason ?? 'not classified yet'}
              </td>
              <td className="num">{((flow.bytes_per_second * 8) / 1e6).toFixed(2)}</td>
              <td className="num">{Math.round(flow.packets_per_second)}</td>
              <td className="num">{flow.flow_rtt_ms?.toFixed(1) ?? '—'} ms</td>
              <td className="num">{(flow.served_fraction * 100).toFixed(0)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
