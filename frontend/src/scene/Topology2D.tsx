/**
 * Two-dimensional topology, used when WebGL is unavailable, when the renderer
 * fails, or when the viewer asks for it.
 *
 * It is driven by exactly the same state as the three-dimensional scene and
 * shows the same facts: device, class, rates and queue occupancy. Nothing is
 * lost by falling back here except the perspective.
 */

import { useMemo } from 'react';

import type { FlowState, MetricsSnapshot, Topology, TrafficClass } from '@/api/types';
import { CLASS_COLOR, CLASS_LABEL, THEME } from '@/lib/colors';
import { bytesPerSecondToMbps, num } from '@/lib/format';

const WIDTH = 900;
const HEIGHT = 520;
/** Scene units to pixels, with the gateway at the centre. */
const SCALE = 62;

function project(x: number, z: number): [number, number] {
  return [WIDTH / 2 + x * SCALE, HEIGHT / 2 + z * SCALE * 0.78];
}

export function Topology2D({
  topology,
  flows,
  snapshot,
  selectedDeviceId,
  onSelectDevice,
}: {
  topology: Topology | null;
  flows: FlowState[];
  snapshot: MetricsSnapshot | null;
  selectedDeviceId: string | null;
  onSelectDevice: (deviceId: string | null) => void;
}) {
  const devices = topology?.devices ?? [];
  const [gatewayX, gatewayY] = project(0, 0);
  const [internetX, internetY] = project(0, -5.4);

  const peak = useMemo(
    () => Math.max(1, ...flows.map((f) => f.bytes_per_second)),
    [flows],
  );

  const perDevice = useMemo(() => {
    const map = new Map<
      string,
      { download: number; upload: number; traffic: TrafficClass; muted: boolean; rtt: number | null }
    >();
    for (const flow of flows) {
      const mbps = bytesPerSecondToMbps(flow.bytes_per_second);
      const entry = map.get(flow.device_id) ?? {
        download: 0,
        upload: 0,
        traffic: flow.traffic_class,
        muted: false,
        rtt: flow.flow_rtt_ms,
      };
      if (flow.direction === 'DOWNLOAD') entry.download += mbps;
      else entry.upload += mbps;
      entry.traffic = flow.traffic_class;
      entry.muted =
        flow.traffic_class === 'UNKNOWN' || flow.classification_source === 'SAFE_FALLBACK';
      entry.rtt = flow.flow_rtt_ms ?? entry.rtt;
      map.set(flow.device_id, entry);
    }
    return map;
  }, [flows]);

  const download = snapshot?.per_direction.find((d) => d.direction === 'DOWNLOAD');
  const occupancy = download?.queue_occupancy_percent ?? 0;
  const queueColor =
    occupancy >= 70 ? THEME.critical : occupancy >= 35 ? THEME.warning : THEME.success;

  if (!topology) {
    return (
      <div className="empty-state" style={{ height: '100%', justifyContent: 'center' }}>
        No topology loaded yet.
      </div>
    );
  }

  return (
    <svg
      className="topology-2d"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Two-dimensional view of the emulated home network topology"
      onClick={() => onSelectDevice(null)}
    >
      {/* Wide area network link */}
      <line
        x1={gatewayX}
        y1={gatewayY}
        x2={internetX}
        y2={internetY}
        stroke={THEME.grid}
        strokeWidth={3}
      />

      {devices
        .filter((d) => d.device_id !== 'gateway' && d.device_id !== 'internet')
        .map((device) => {
          const [x, y] = project(device.scene_x, device.scene_z);
          const entry = perDevice.get(device.device_id);
          const color = entry
            ? entry.muted
              ? CLASS_COLOR.UNKNOWN
              : CLASS_COLOR[entry.traffic]
            : THEME.grid;
          const total = (entry?.download ?? 0) + (entry?.upload ?? 0);
          const width = 1.5 + Math.min(5, (total / bytesPerSecondToMbps(peak)) * 5);
          return (
            <line
              key={`link-${device.device_id}`}
              x1={gatewayX}
              y1={gatewayY}
              x2={x}
              y2={y}
              stroke={color}
              strokeWidth={width}
              strokeOpacity={entry ? 0.6 : 0.2}
            />
          );
        })}

      {/* Internet */}
      <g>
        <circle
          cx={internetX}
          cy={internetY}
          r={26}
          fill="#13403f"
          stroke={THEME.info}
          strokeWidth={1.5}
        />
        <text
          x={internetX}
          y={internetY - 36}
          textAnchor="middle"
          fill={THEME.champagne}
          fontSize={13}
        >
          Internet
        </text>
      </g>

      {/* Gateway */}
      <g>
        <circle
          cx={gatewayX}
          cy={gatewayY}
          r={34}
          fill="#1c3331"
          stroke={THEME.bronze}
          strokeWidth={2}
        />
        <text
          x={gatewayX}
          y={gatewayY + 56}
          textAnchor="middle"
          fill={THEME.bronze}
          fontSize={13}
        >
          QoSense Gateway
        </text>
      </g>

      {/* Download queue beside the gateway */}
      <g transform={`translate(${gatewayX + 110}, ${gatewayY - 70})`}>
        <text x={0} y={-12} fill={THEME.muted} fontSize={11}>
          Download queue
        </text>
        {Array.from({ length: 10 }, (_, index) => {
          const filled = index < Math.round((occupancy / 100) * 10);
          return (
            <rect
              key={index}
              x={0}
              y={(9 - index) * 13}
              width={34}
              height={10}
              rx={2}
              fill={filled ? queueColor : '#15302e'}
              fillOpacity={filled ? 0.95 : 0.4}
            />
          );
        })}
        <text x={44} y={60} fill={queueColor} fontSize={12} fontFamily="monospace">
          {num(occupancy, 0)}%
        </text>
        <text x={44} y={76} fill={THEME.muted} fontSize={11} fontFamily="monospace">
          +{num(download?.queue_delay_ms, 1)} ms
        </text>
        {snapshot?.congestion_state === 'BUFFERBLOAT' && (
          <text x={0} y={152} fill={THEME.critical} fontSize={11}>
            Bufferbloat measured
          </text>
        )}
      </g>

      {/* Devices */}
      {devices
        .filter((d) => d.device_id !== 'gateway' && d.device_id !== 'internet')
        .map((device) => {
          const [x, y] = project(device.scene_x, device.scene_z);
          const entry = perDevice.get(device.device_id);
          const color = entry
            ? entry.muted
              ? CLASS_COLOR.UNKNOWN
              : CLASS_COLOR[entry.traffic]
            : THEME.faint;
          const selected = selectedDeviceId === device.device_id;
          return (
            <g
              key={device.device_id}
              onClick={(event) => {
                event.stopPropagation();
                onSelectDevice(selected ? null : device.device_id);
              }}
              style={{ cursor: 'pointer' }}
            >
              <rect
                x={x - 26}
                y={y - 18}
                width={52}
                height={36}
                rx={5}
                fill="#1b3330"
                stroke={selected ? THEME.bronze : color}
                strokeWidth={selected ? 2 : 1.2}
              />
              <text x={x} y={y + 34} textAnchor="middle" fill={THEME.champagne} fontSize={12}>
                {device.label}
              </text>
              {entry && (
                <>
                  <text x={x} y={y + 48} textAnchor="middle" fill={color} fontSize={10}>
                    {entry.muted ? 'Unknown' : CLASS_LABEL[entry.traffic]}
                  </text>
                  <text
                    x={x}
                    y={y + 61}
                    textAnchor="middle"
                    fill={THEME.muted}
                    fontSize={10}
                    fontFamily="monospace"
                  >
                    ↓{num(entry.download, 1)} ↑{num(entry.upload, 1)} Mbps
                  </text>
                </>
              )}
            </g>
          );
        })}
    </svg>
  );
}
