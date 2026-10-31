/**
 * Panels for the observation half of the pipeline: network health, the flow
 * observer (M1) and the two model cards.
 */

import type {
  ClassificationResult,
  CongestionPrediction,
  FlowFeatureWindow,
  FlowState,
  MetricsSnapshot,
  ModelStatus,
} from '@/api/types';
import { Gauge, JitterArrivalTimeline } from '@/components/charts';
import {
  Badge,
  CheckList,
  EmptyState,
  KeyValue,
  Notice,
  Panel,
} from '@/components/primitives';
import { CLASS_COLOR, CLASS_LABEL, THEME, congestionColor, modelStateTone } from '@/lib/colors';
import { humanise, num } from '@/lib/format';

export function NetworkHealthPanel({ snapshot }: { snapshot: MetricsSnapshot | null }) {
  const congestion = snapshot?.congestion_state ?? 'UNKNOWN';
  return (
    <Panel
      title="Network health"
      hint="Measured this interval"
      actions={
        <Badge tone={congestion === 'CLEAR' ? 'success' : congestion === 'UNKNOWN' ? 'muted' : 'warning'}>
          {humanise(congestion)}
        </Badge>
      }
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(72px, 1fr))',
          gap: 'var(--gap-sm)',
        }}
      >
        <Gauge
          label="Round-trip time"
          value={snapshot?.rtt_ms ?? null}
          unit="ms"
          max={200}
          color={THEME.warning}
        />
        <Gauge
          label="Jitter"
          value={snapshot?.jitter_ms ?? null}
          unit="ms"
          max={40}
          color={THEME.success}
        />
        <Gauge
          label="Packet loss"
          value={snapshot?.packet_loss_percent ?? null}
          unit="%"
          max={5}
          color={THEME.critical}
        />
        <Gauge
          label="Queue occupancy"
          value={snapshot?.queue_occupancy_percent ?? null}
          unit="%"
          max={100}
          color={congestionColor(congestion)}
        />
      </div>
      <KeyValue
        rows={[
          ['Idle baseline round-trip time', `${num(snapshot?.idle_baseline_rtt_ms, 1)} ms`],
          ['Added delay over baseline', `${num(snapshot?.added_delay_ms, 1)} ms`],
          ['Queue depth', `${snapshot?.queue_packets ?? '—'} packets`],
          ['Active flows', String(snapshot?.active_flows ?? 0)],
        ]}
      />
      <p className="small faint" style={{ margin: 0 }}>
        Round-trip time is a probe measurement, not one-way application latency. Added delay is
        the loaded round-trip time minus the idle baseline measured by the same probe.
      </p>
    </Panel>
  );
}

export function TrafficFlowPanel({
  flows,
  featureWindows,
  selectedFlowId,
}: {
  flows: FlowState[];
  featureWindows: Record<string, FlowFeatureWindow>;
  selectedFlowId: string | null;
}) {
  const flow =
    flows.find((f) => f.flow_id === selectedFlowId) ??
    [...flows].sort((a, b) => b.bytes_per_second - a.bytes_per_second)[0];
  const window = flow ? featureWindows[flow.flow_id] : undefined;

  return (
    <Panel
      title="Traffic flow detected"
      step={1}
      hint={flow ? flow.flow_id : 'No flows'}
    >
      {!flow || !window ? (
        <EmptyState
          title="No feature window yet."
          hint="The observer aggregates packets into a two-second window before anything is classified."
        />
      ) : (
        <>
          <KeyValue
            rows={[
              ['Device', flow.device_label],
              ['Direction', flow.direction === 'DOWNLOAD' ? 'Download' : 'Upload'],
              ['Transport protocol', window.protocol],
              ['Mean packet size', `${num(window.features.mean_packet_bytes, 0)} B`],
              ['Packet size spread', `±${num(window.features.std_packet_bytes, 0)} B`],
              ['Mean gap between packets', `${num(window.features.mean_interarrival_ms, 2)} ms`],
              ['Gap variation', `±${num(window.features.std_interarrival_ms, 2)} ms`],
              ['Packets per second', num(window.features.packets_per_second, 0)],
              ['Active time ratio', num(window.features.active_time_ratio, 2)],
              ['Bursts in window', String(window.features.burst_count)],
              ['Flow duration', `${num(window.features.flow_duration_ms / 1000, 1)} s`],
            ]}
          />
          <p className="small faint" style={{ margin: 0 }}>
            Feature schema {window.feature_schema_version}, {window.window_ms} ms window. No
            application payload is read or stored.
          </p>
        </>
      )}
    </Panel>
  );
}

export function TrafficClassifierPanel({
  status,
  classification,
  flow,
}: {
  status: ModelStatus | undefined;
  classification: ClassificationResult | undefined;
  flow: FlowState | undefined;
}) {
  const ready = status?.state === 'READY';

  return (
    <Panel
      title="Traffic classification"
      step={2}
      actions={
        <Badge tone={status ? modelStateTone(status.state) : 'muted'} dot>
          {status ? humanise(status.state) : 'Unknown'}
        </Badge>
      }
    >
      <KeyValue
        rows={[
          ['Model', status?.display_name ?? 'Model 1'],
          ['Algorithm', status?.algorithm ?? 'LightGBM multiclass'],
          ['Version', status?.version ?? '—'],
          ['Feature schema', status?.feature_schema_version ?? '—'],
          ['Inferences run', String(status?.inference_count ?? 0)],
          ['Phase', `${status?.implementation_phase ?? '—'} · ${status?.round_allocation ?? '—'}`],
        ]}
      />

      {!ready && (
        <Notice tone="warning">
          {status?.detail ??
            'Model 1 is not loaded, so no model prediction is being produced.'}
        </Notice>
      )}

      {classification && flow ? (
        <>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 'var(--gap-sm)',
            }}
          >
            <span className="small faint">Current label for {flow.flow_id}</span>
            <span
              style={{
                color: CLASS_COLOR[classification.predicted_class],
                fontWeight: 600,
              }}
            >
              {CLASS_LABEL[classification.predicted_class]}
            </span>
          </div>
          <KeyValue
            rows={[
              [
                'Produced by',
                classification.source === 'MODEL_1'
                  ? 'Model 1'
                  : classification.source === 'DETERMINISTIC_RULES'
                    ? 'Deterministic rule set'
                    : 'Safe fallback',
              ],
              ['Reason', <span className="small">{classification.reason}</span>],
              ['Decision', humanise(classification.decision)],
              [
                'Calibrated confidence',
                classification.calibrated_confidence === null ? (
                  <span className="faint">Not produced</span>
                ) : (
                  `${(classification.calibrated_confidence * 100).toFixed(0)}%`
                ),
              ],
              ['Calibration', humanise(classification.calibration_status)],
              [
                'Inference time',
                classification.inference_ms === null
                  ? '—'
                  : `${classification.inference_ms.toFixed(3)} ms`,
              ],
            ]}
          />
          {Object.keys(classification.probabilities).length === 0 && (
            <p className="small faint" style={{ margin: 0 }}>
              No probability distribution is shown because none was produced. A rule set returns
              a class, not a probability, and an untrained model returns nothing at all.
            </p>
          )}
        </>
      ) : (
        <EmptyState title="No classification yet." />
      )}
    </Panel>
  );
}

export function CongestionPredictorPanel({
  status,
  prediction,
  measured,
}: {
  status: ModelStatus | undefined;
  prediction: CongestionPrediction | null;
  measured: MetricsSnapshot | null;
}) {
  const unavailable = !prediction || prediction.risk_band === 'UNAVAILABLE';
  const measuredState = measured?.congestion_state ?? 'UNKNOWN';

  return (
    <Panel
      title="Congestion prediction"
      step={3}
      actions={
        <Badge tone={status ? modelStateTone(status.state) : 'muted'} dot>
          {status ? humanise(status.state) : 'Unknown'}
        </Badge>
      }
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 'var(--gap-sm)',
        }}
      >
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            padding: 'var(--gap-sm)',
          }}
        >
          <div className="small faint">Measured now</div>
          <div style={{ color: congestionColor(measuredState), fontWeight: 600 }}>
            {humanise(measuredState)}
          </div>
          <div className="small faint mono">
            +{num(measured?.added_delay_ms, 1)} ms · {num(measured?.queue_occupancy_percent, 0)}%
            queue
          </div>
        </div>
        <div
          style={{
            border: '1px dashed var(--border-strong)',
            borderRadius: 'var(--radius-sm)',
            padding: 'var(--gap-sm)',
          }}
        >
          <div className="small faint">Predicted ahead</div>
          <div style={{ color: unavailable ? 'var(--text-faint)' : 'var(--warning)', fontWeight: 600 }}>
            {unavailable ? 'Unavailable' : humanise(prediction.risk_band)}
          </div>
          <div className="small faint mono">
            {prediction?.prediction_horizon_seconds
              ? `horizon ${prediction.prediction_horizon_seconds} s`
              : 'no horizon defined'}
          </div>
        </div>
      </div>

      <p className="small faint" style={{ margin: 0 }}>
        Measured congestion and predicted risk are separate quantities and are never derived
        from one another. A prediction describes a future interval; it is not evidence that a
        queue is failing now.
      </p>

      <Notice tone={unavailable ? 'warning' : 'info'}>
        {status?.detail ??
          'Model 2 is designed in docs/ML_SPEC.md and scheduled for Phase 5 in Round 2.'}
      </Notice>

      {prediction && (
        <KeyValue
          rows={[
            ['Probability', prediction.congestion_probability === null ? 'Not produced' : num(prediction.congestion_probability, 3)],
            ['Controller decision', humanise(prediction.decision)],
            ['Reason', <span className="small">{prediction.reason}</span>],
            ['Version', prediction.model_version],
          ]}
        />
      )}
    </Panel>
  );
}

export function JitterPanel({ snapshot }: { snapshot: MetricsSnapshot | null }) {
  return (
    <Panel title="Packet arrival spacing" hint="Delay variation">
      <JitterArrivalTimeline
        jitterMs={snapshot?.jitter_ms ?? null}
        rttMs={snapshot?.rtt_ms ?? null}
      />
    </Panel>
  );
}

export function ModelSummaryList({ models }: { models: ModelStatus[] }) {
  if (models.length === 0) return <EmptyState title="No model information." />;
  return (
    <CheckList
      checks={models.map((model) => ({
        name: model.display_name,
        passed: model.state === 'READY',
        detail: model.detail,
      }))}
    />
  );
}
