/**
 * Flow table for the Live Traffic page and the overview.
 *
 * Confidence is shown as what it actually is. The deterministic rule set has
 * no probability distribution, so its rows say "rule-based" rather than
 * displaying a number that was never computed.
 */

import { useMemo, useState } from 'react';

import type { ClassificationResult, Direction, FlowState, TrafficClass } from '@/api/types';
import { CLASS_COLOR, CLASS_LABEL, CLASS_ORDER } from '@/lib/colors';
import { NOT_MEASURED, bytesPerSecondToMbps, clockTime, integer, num } from '@/lib/format';
import { Badge, EmptyState } from './primitives';

export interface FlowTableProps {
  flows: FlowState[];
  classifications: Record<string, ClassificationResult>;
  selectedFlowId: string | null;
  onSelect: (flowId: string | null) => void;
  compact?: boolean;
  showFilters?: boolean;
}

export function FlowTable({
  flows,
  classifications,
  selectedFlowId,
  onSelect,
  compact = false,
  showFilters = false,
}: FlowTableProps) {
  const [classFilter, setClassFilter] = useState<TrafficClass | 'ALL'>('ALL');
  const [directionFilter, setDirectionFilter] = useState<Direction | 'ALL'>('ALL');
  const [deviceFilter, setDeviceFilter] = useState<string>('ALL');
  const [knownOnly, setKnownOnly] = useState(false);

  const devices = useMemo(
    () => Array.from(new Set(flows.map((f) => f.device_label))).sort(),
    [flows],
  );

  const visible = useMemo(
    () =>
      flows.filter((flow) => {
        if (classFilter !== 'ALL' && flow.traffic_class !== classFilter) return false;
        if (directionFilter !== 'ALL' && flow.direction !== directionFilter) return false;
        if (deviceFilter !== 'ALL' && flow.device_label !== deviceFilter) return false;
        if (knownOnly && flow.traffic_class === 'UNKNOWN') return false;
        return true;
      }),
    [flows, classFilter, directionFilter, deviceFilter, knownOnly],
  );

  if (flows.length === 0) {
    return (
      <EmptyState
        title="No active flows."
        hint="Flows appear once an experiment is running and its generators have started."
      />
    );
  }

  return (
    <>
      {showFilters && (
        <div className="field-row">
          <label className="field">
            <span className="field__label">Class</span>
            <select
              value={classFilter}
              onChange={(e) => setClassFilter(e.target.value as TrafficClass | 'ALL')}
            >
              <option value="ALL">All classes</option>
              {CLASS_ORDER.map((traffic) => (
                <option key={traffic} value={traffic}>
                  {CLASS_LABEL[traffic]}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field__label">Direction</span>
            <select
              value={directionFilter}
              onChange={(e) => setDirectionFilter(e.target.value as Direction | 'ALL')}
            >
              <option value="ALL">Both directions</option>
              <option value="DOWNLOAD">Download</option>
              <option value="UPLOAD">Upload</option>
            </select>
          </label>
          <label className="field">
            <span className="field__label">Device</span>
            <select value={deviceFilter} onChange={(e) => setDeviceFilter(e.target.value)}>
              <option value="ALL">All devices</option>
              {devices.map((device) => (
                <option key={device} value={device}>
                  {device}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field__label">Classification</span>
            <select
              value={knownOnly ? 'known' : 'all'}
              onChange={(e) => setKnownOnly(e.target.value === 'known')}
            >
              <option value="all">Include unknown flows</option>
              <option value="known">Classified flows only</option>
            </select>
          </label>
        </div>
      )}

      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Flow</th>
              <th scope="col">Device</th>
              <th scope="col">Dir</th>
              <th scope="col">Class</th>
              <th scope="col">Confidence</th>
              <th scope="col" className="num">
                Mbps
              </th>
              <th scope="col" className="num">
                Packets/s
              </th>
              <th scope="col" className="num">
                Flow delay
              </th>
              {!compact && (
                <>
                  <th scope="col">Queue class</th>
                  <th scope="col" className="num">
                    Served
                  </th>
                  <th scope="col" className="num">
                    Total
                  </th>
                  <th scope="col">Source</th>
                  <th scope="col">Updated</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {visible.map((flow) => {
              const classification = classifications[flow.flow_id];
              const selected = selectedFlowId === flow.flow_id;
              return (
                <tr
                  key={flow.flow_id}
                  aria-selected={selected}
                  onClick={() => onSelect(selected ? null : flow.flow_id)}
                  style={{ cursor: 'pointer' }}
                >
                  <td className="mono">{flow.flow_id}</td>
                  <td>{flow.device_label}</td>
                  <td>
                    <span title={flow.direction === 'DOWNLOAD' ? 'Download' : 'Upload'}>
                      {flow.direction === 'DOWNLOAD' ? '↓' : '↑'}
                    </span>
                  </td>
                  <td>
                    <span
                      style={{
                        color: CLASS_COLOR[flow.traffic_class],
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 5,
                      }}
                    >
                      <span
                        className="legend__swatch"
                        style={{ background: CLASS_COLOR[flow.traffic_class] }}
                        aria-hidden="true"
                      />
                      {CLASS_LABEL[flow.traffic_class]}
                    </span>
                  </td>
                  <td>
                    <ConfidenceCell classification={classification} />
                  </td>
                  <td className="num">{num(bytesPerSecondToMbps(flow.bytes_per_second), 2)}</td>
                  <td className="num">{integer(flow.packets_per_second)}</td>
                  <td className="num">{num(flow.flow_rtt_ms, 1)} ms</td>
                  {!compact && (
                    <>
                      <td className="mono small">{flow.queue_class}</td>
                      <td className="num">{num(flow.served_fraction * 100, 0)}%</td>
                      <td className="num">{num(flow.total_bytes / 1e6, 1)} MB</td>
                      <td className="small">
                        {flow.classification_source === 'MODEL_1'
                          ? 'Model 1'
                          : flow.classification_source === 'DETERMINISTIC_RULES'
                            ? 'Rules'
                            : 'Fallback'}
                      </td>
                      <td className="small faint">{clockTime(flow.last_update)}</td>
                    </>
                  )}
                </tr>
              );
            })}
            {visible.length === 0 && (
              <tr>
                <td colSpan={compact ? 8 : 13}>
                  <span className="faint">No flows match the current filters.</span>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ConfidenceCell({ classification }: { classification?: ClassificationResult }) {
  if (!classification) {
    return <span className="faint">{NOT_MEASURED}</span>;
  }
  if (classification.source === 'SAFE_FALLBACK') {
    return (
      <Badge tone="warning" title={classification.reason}>
        Safe fallback
      </Badge>
    );
  }
  if (classification.calibrated_confidence === null) {
    return (
      <span
        className="faint small"
        title={
          'The deterministic rule set produces a class, not a probability distribution. ' +
          `Matched rule: ${classification.reason}.`
        }
      >
        Rule-based
      </span>
    );
  }
  return (
    <span className="mono">
      {(classification.calibrated_confidence * 100).toFixed(0)}%
      {classification.calibration_status !== 'CALIBRATED' && (
        <span className="faint small" title="Probabilities have not been calibrated.">
          {' '}
          uncal.
        </span>
      )}
    </span>
  );
}
