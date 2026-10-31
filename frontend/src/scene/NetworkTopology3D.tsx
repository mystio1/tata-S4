/**
 * The central three-dimensional home network topology.
 *
 * The scene is a view of backend state, never a source of it. Every stream,
 * every queue block and every highlight traces back to a metrics snapshot or
 * a typed event. The camera is fixed with limited orbit, animation stops when
 * the tab is hidden or reduced motion is requested, and a two-dimensional
 * fallback takes over when WebGL is unavailable.
 */

import {
  Component,
  type ErrorInfo,
  type ReactNode,
  Suspense,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import * as THREE from 'three';

import type {
  FlowState,
  MetricsSnapshot,
  Topology,
  TrafficClass,
} from '@/api/types';
import { CLASS_COLOR, CLASS_LABEL, CLASS_ORDER } from '@/lib/colors';
import { bytesPerSecondToMbps } from '@/lib/format';
import { PacketStream } from './PacketStream';
import {
  DeviceNode,
  type DeviceFlowSummary,
  GatewayModel,
  GroundPlane,
  InternetNode,
  QueueStack,
} from './nodes';
import { Topology2D } from './Topology2D';
import { detectWebgl, useSceneMotion } from './useSceneMotion';

const GATEWAY = new THREE.Vector3(0, 0.62, 0);
const INTERNET = new THREE.Vector3(0, 2.1, -5.4);
const DEVICE_HEIGHT = 0.42;

export interface NetworkTopology3DProps {
  topology: Topology | null;
  flows: FlowState[];
  snapshot: MetricsSnapshot | null;
  shapingActive: boolean;
  highlightedFlowIds: Set<string>;
  policyPulse: boolean;
  selectedDeviceId: string | null;
  onSelectDevice: (deviceId: string | null) => void;
  prefer2D: boolean;
  onWebglFailure: () => void;
}

interface StreamSpec {
  key: string;
  path: THREE.Vector3[];
  trafficClass: TrafficClass;
  packetsPerSecond: number;
  intensity: number;
  muted: boolean;
  highlight: boolean;
}

function devicePosition(scene_x: number, scene_z: number): THREE.Vector3 {
  return new THREE.Vector3(scene_x, DEVICE_HEIGHT, scene_z);
}

function Scene({
  topology,
  flows,
  snapshot,
  shapingActive,
  highlightedFlowIds,
  policyPulse,
  selectedDeviceId,
  onSelectDevice,
  animate,
}: Omit<NetworkTopology3DProps, 'prefer2D' | 'onWebglFailure'> & { animate: boolean }) {
  const devices = topology?.devices.filter((d) => d.device_id !== 'internet') ?? [];

  const peakBytesPerSecond = useMemo(
    () => Math.max(1, ...flows.map((flow) => flow.bytes_per_second)),
    [flows],
  );

  const streams = useMemo<StreamSpec[]>(() => {
    const byId = new Map(topology?.devices.map((d) => [d.device_id, d]) ?? []);
    const specs: StreamSpec[] = [];

    for (const flow of flows) {
      const device = byId.get(flow.device_id);
      if (!device || flow.packets_per_second <= 0) continue;
      const node = devicePosition(device.scene_x, device.scene_z);
      // Upload runs device to gateway to Internet; download runs the reverse.
      const path =
        flow.direction === 'UPLOAD'
          ? [node, GATEWAY.clone(), INTERNET.clone()]
          : [INTERNET.clone(), GATEWAY.clone(), node];

      specs.push({
        key: flow.flow_id,
        path,
        trafficClass: flow.traffic_class,
        packetsPerSecond: flow.packets_per_second,
        intensity: Math.min(1, flow.bytes_per_second / peakBytesPerSecond),
        // A flow the controller could not classify is drawn muted grey, which
        // is how `docs/UI_SPEC.md` asks low-confidence flows to appear.
        muted:
          flow.traffic_class === 'UNKNOWN' ||
          flow.classification_source === 'SAFE_FALLBACK',
        highlight: highlightedFlowIds.has(flow.flow_id),
      });
    }
    return specs;
  }, [flows, topology, peakBytesPerSecond, highlightedFlowIds]);

  const summaries = useMemo(() => {
    const result = new Map<string, DeviceFlowSummary>();
    for (const flow of flows) {
      const existing = result.get(flow.device_id);
      const mbps = bytesPerSecondToMbps(flow.bytes_per_second);
      const isDown = flow.direction === 'DOWNLOAD';
      const muted =
        flow.traffic_class === 'UNKNOWN' || flow.classification_source === 'SAFE_FALLBACK';
      if (!existing) {
        result.set(flow.device_id, {
          trafficClass: flow.traffic_class,
          downloadMbps: isDown ? mbps : 0,
          uploadMbps: isDown ? 0 : mbps,
          rttMs: flow.flow_rtt_ms,
          muted,
        });
      } else {
        existing.downloadMbps = (existing.downloadMbps ?? 0) + (isDown ? mbps : 0);
        existing.uploadMbps = (existing.uploadMbps ?? 0) + (isDown ? 0 : mbps);
        existing.rttMs = flow.flow_rtt_ms ?? existing.rttMs;
      }
    }
    return result;
  }, [flows]);

  const congestionState = snapshot?.congestion_state ?? 'UNKNOWN';
  const downloadDirection = snapshot?.per_direction.find((d) => d.direction === 'DOWNLOAD');

  return (
    <>
      <ambientLight intensity={0.55} />
      <directionalLight position={[6, 11, 5]} intensity={0.85} castShadow />
      <directionalLight position={[-7, 6, -4]} intensity={0.3} color="#4fcbd0" />
      <pointLight position={[0, 3.2, 0]} intensity={14} distance={9} color="#c69a5b" />

      <GroundPlane />

      <GatewayModel shaping={shapingActive} animate={animate} pulse={policyPulse} />
      <InternetNode
        position={[INTERNET.x, INTERNET.y, INTERNET.z]}
        congested={congestionState === 'CONGESTED' || congestionState === 'BUFFERBLOAT'}
        animate={animate}
      />

      <QueueStack
        position={[2.0, 0, -1.5]}
        occupancyPercent={downloadDirection?.queue_occupancy_percent ?? null}
        queuePackets={downloadDirection?.queue_packets ?? null}
        queueDelayMs={downloadDirection?.queue_delay_ms ?? null}
        bufferbloat={congestionState === 'BUFFERBLOAT'}
        animate={animate}
      />

      {devices.map((device) => (
        <DeviceNode
          key={device.device_id}
          position={[device.scene_x, 0, device.scene_z]}
          label={device.label}
          kind={device.kind}
          summary={summaries.get(device.device_id) ?? null}
          selected={selectedDeviceId === device.device_id}
          highlighted={flows.some(
            (f) => f.device_id === device.device_id && highlightedFlowIds.has(f.flow_id),
          )}
          onSelect={() =>
            onSelectDevice(selectedDeviceId === device.device_id ? null : device.device_id)
          }
        />
      ))}

      {streams.map((stream) => (
        <PacketStream
          key={stream.key}
          path={stream.path}
          trafficClass={stream.trafficClass}
          packetsPerSecond={stream.packetsPerSecond}
          intensity={stream.intensity}
          muted={stream.muted}
          highlight={stream.highlight}
          animate={animate}
        />
      ))}

      <OrbitControls
        enablePan={false}
        enableZoom
        minDistance={10}
        maxDistance={22}
        minPolarAngle={Math.PI / 7}
        maxPolarAngle={Math.PI / 2.45}
        minAzimuthAngle={-Math.PI / 4}
        maxAzimuthAngle={Math.PI / 4}
        target={[0, 0.6, 0.4]}
        enableDamping
        dampingFactor={0.08}
      />
    </>
  );
}

export function NetworkTopology3D(props: NetworkTopology3DProps) {
  const { animate, reducedMotion, hidden } = useSceneMotion();
  const [webglAvailable] = useState(detectWebgl);
  const [crashed, setCrashed] = useState(false);
  const { onWebglFailure } = props;

  // Depend on the callback itself, not on the props object. The props object
  // is a fresh reference every render, so depending on it would re-run this
  // effect on each render and loop through the store update it performs.
  useEffect(() => {
    if (!webglAvailable) onWebglFailure();
  }, [webglAvailable, onWebglFailure]);

  const useFallback = !webglAvailable || crashed || props.prefer2D;

  if (useFallback) {
    return (
      <div className="scene-shell">
        <Topology2D
          topology={props.topology}
          flows={props.flows}
          snapshot={props.snapshot}
          selectedDeviceId={props.selectedDeviceId}
          onSelectDevice={props.onSelectDevice}
        />
        <div className="scene-overlay scene-overlay--top-right">
          <span className="badge badge--muted">
            {!webglAvailable
              ? 'Two-dimensional view · WebGL unavailable'
              : crashed
                ? 'Two-dimensional view · 3D renderer failed'
                : 'Two-dimensional view'}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="scene-shell">
      <Canvas
        shadows
        dpr={[1, 1.8]}
        camera={{ position: [0, 9.2, 11.4], fov: 38 }}
        // Pausing the render loop while the tab is hidden keeps a background
        // tab from burning frames. Experiment timing is unaffected: the
        // simulator advances on the server.
        frameloop={animate ? 'always' : 'demand'}
        onCreated={({ gl }) => {
          gl.setClearColor('#081c1d', 0);
        }}
        onPointerMissed={() => props.onSelectDevice(null)}
        fallback={<div className="empty-state">Preparing the scene…</div>}
      >
        <Suspense fallback={null}>
          <ErrorTrap onError={() => setCrashed(true)}>
            <Scene {...props} animate={animate} />
          </ErrorTrap>
        </Suspense>
      </Canvas>

      <div className="scene-overlay scene-overlay--top-right">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-end' }}>
          {reducedMotion && <span className="badge badge--muted">Reduced motion</span>}
          {hidden && <span className="badge badge--muted">Paused: tab hidden</span>}
        </div>
      </div>

      <div className="scene-overlay scene-overlay--bottom-left">
        <div className="legend" style={{ maxWidth: 420 }}>
          {CLASS_ORDER.map((traffic) => (
            <span className="legend__item" key={traffic}>
              <span
                className="legend__swatch"
                style={{ background: CLASS_COLOR[traffic] }}
                aria-hidden="true"
              />
              {CLASS_LABEL[traffic]}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * Keeps a renderer failure from taking the whole dashboard down. The charts
 * and controls have to stay usable even if the scene cannot draw.
 */
class ErrorTrap extends Component<
  { children: ReactNode; onError: () => void },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('QoSense scene failed to render', error, info);
    this.props.onError();
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}
