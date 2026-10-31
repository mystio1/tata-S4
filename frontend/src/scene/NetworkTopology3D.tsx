/**
 * The central three-dimensional home network topology.
 *
 * The scene is a view of backend state, never a source of it. Every stream,
 * every queue block and every highlight traces back to a metrics snapshot or
 * a typed event. The camera follows a short cinematic loop; animation stops
 * when the tab is hidden or reduced motion is requested, and a two-dimensional
 * fallback takes over when WebGL is unavailable.
 */

import {
  Component,
  type ErrorInfo,
  type ReactNode,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
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
const INTERNET = new THREE.Vector3(0, 4.15, -1.15);
const DEVICE_HEIGHT = 0.42;
const CAMERA_CYCLE_SECONDS = 10;

const CAMERA_SHOTS = [
  {
    time: 0,
    position: new THREE.Vector3(0, 10.8, 15.6),
    target: new THREE.Vector3(0, 1.2, 0),
  },
  {
    time: 2.1,
    position: new THREE.Vector3(5.1, 6.2, 10.8),
    target: new THREE.Vector3(0, 1.9, -0.8),
  },
  {
    time: 4.1,
    position: new THREE.Vector3(5.8, 4.6, 5.1),
    target: new THREE.Vector3(0, 1.5, -0.5),
  },
  {
    time: 6.2,
    position: new THREE.Vector3(-5.4, 4.9, 5.8),
    target: new THREE.Vector3(0, 1.1, 0),
  },
  {
    time: 8.1,
    position: new THREE.Vector3(-3.2, 8.2, 13.2),
    target: new THREE.Vector3(0, 1.3, 0),
  },
  {
    time: CAMERA_CYCLE_SECONDS,
    position: new THREE.Vector3(0, 10.8, 15.6),
    target: new THREE.Vector3(0, 1.2, 0),
  },
];

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

function CameraRig({ animate }: { animate: boolean }) {
  const { camera } = useThree();
  const lookTarget = useRef(new THREE.Vector3());
  const fixedPosition = useMemo(() => new THREE.Vector3(0, 10.8, 15.6), []);
  const scratchPosition = useMemo(() => new THREE.Vector3(), []);

  useFrame((state) => {
    if (!animate) {
      camera.position.lerp(fixedPosition, 0.04);
      camera.lookAt(0, 1.2, 0);
      return;
    }

    const time = state.clock.elapsedTime % CAMERA_CYCLE_SECONDS;
    let shotIndex = 0;
    while (shotIndex < CAMERA_SHOTS.length - 2 && time > CAMERA_SHOTS[shotIndex + 1].time) {
      shotIndex += 1;
    }

    const from = CAMERA_SHOTS[shotIndex];
    const to = CAMERA_SHOTS[shotIndex + 1];
    const progress = (time - from.time) / (to.time - from.time);
    const eased = 0.5 - Math.cos(Math.PI * progress) * 0.5;
    scratchPosition.copy(from.position).lerp(to.position, eased);
    lookTarget.current.copy(from.target).lerp(to.target, eased);
    camera.position.copy(scratchPosition);
    camera.lookAt(lookTarget.current);
  });

  return null;
}

function RoomBackdrop() {
  return (
    <group>
      <mesh position={[0, 2.8, -8.6]} receiveShadow>
        <boxGeometry args={[21, 6.2, 0.35]} />
        <meshStandardMaterial color="#102523" roughness={0.86} metalness={0.18} />
      </mesh>
      <mesh position={[0, 3.25, -8.38]}>
        <boxGeometry args={[8.2, 2.65, 0.08]} />
        <meshStandardMaterial
          color="#102b31"
          emissive="#214751"
          emissiveIntensity={0.5}
          roughness={0.2}
          metalness={0.35}
        />
      </mesh>
      {[
        { position: [-4.18, 3.25, -8.29] as const, size: [0.1, 2.75, 0.12] as const },
        { position: [4.18, 3.25, -8.29] as const, size: [0.1, 2.75, 0.12] as const },
        { position: [0, 1.89, -8.29] as const, size: [8.4, 0.1, 0.12] as const },
        { position: [0, 4.6, -8.29] as const, size: [8.4, 0.1, 0.12] as const },
      ].map((frame) => (
        <mesh key={frame.position.join(':')} position={frame.position}>
          <boxGeometry args={[frame.size[0], frame.size[1], frame.size[2]]} />
          <meshStandardMaterial color="#98734c" metalness={0.7} roughness={0.32} />
        </mesh>
      ))}
      {[-9.8, 9.8].map((x) => (
        <mesh key={x} position={[x, 2.8, 0]}>
          <boxGeometry args={[0.3, 6.2, 17.4]} />
          <meshStandardMaterial color="#0b201f" roughness={0.9} metalness={0.12} />
        </mesh>
      ))}
      {[-7.4, 7.4].map((x) => (
        <group key={x} position={[x, 0, -5.6]}>
          <mesh position={[0, 0.08, 0]}>
            <boxGeometry args={[1.3, 0.16, 1.25]} />
            <meshStandardMaterial color="#26312b" roughness={0.8} />
          </mesh>
          <mesh position={[0, 0.82, 0]}>
            <cylinderGeometry args={[0.13, 0.2, 1.35, 8]} />
            <meshStandardMaterial color="#42362a" roughness={0.9} />
          </mesh>
          {[-1, 1].map((side) => (
            <mesh key={side} position={[side * 0.28, 1.3, 0]} rotation={[0, 0, side * 0.5]}>
              <coneGeometry args={[0.42, 0.9, 7]} />
              <meshStandardMaterial color={side < 0 ? '#21483d' : '#2d5747'} roughness={0.95} />
            </mesh>
          ))}
        </group>
      ))}
      {[-7.5, -2.5, 2.5, 7.5].map((x) => (
        <group key={x} position={[x, 3.5, -8.28]}>
          <mesh>
            <boxGeometry args={[0.18, 0.42, 0.12]} />
            <meshStandardMaterial
              color="#d6aa70"
              emissive="#e4a866"
              emissiveIntensity={1.4}
              toneMapped={false}
            />
          </mesh>
          <pointLight position={[0, -0.2, 0.45]} color="#e5a56b" intensity={5} distance={4} />
        </group>
      ))}
      <pointLight position={[0, 5.2, -3.8]} intensity={24} distance={19} color="#d7a468" />
      <pointLight position={[-5.5, 4.2, 1]} intensity={11} distance={16} color="#52d4d0" />
      <pointLight position={[5.5, 4.2, 1]} intensity={10} distance={16} color="#d2a4ff" />
    </group>
  );
}

function NetworkRail({
  path,
  active,
  color,
}: {
  path: THREE.Vector3[];
  active: boolean;
  color: string;
}) {
  const curve = useMemo(() => new THREE.CatmullRomCurve3(path), [path]);
  const geometry = useMemo(
    () => new THREE.TubeGeometry(curve, 40, active ? 0.045 : 0.026, 8, false),
    [curve, active],
  );

  useEffect(() => () => geometry.dispose(), [geometry]);

  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial
        color={active ? color : '#42615b'}
        emissive={active ? color : '#193d3a'}
        emissiveIntensity={active ? 0.9 : 0.28}
        metalness={0.72}
        roughness={0.28}
        transparent
        opacity={active ? 0.94 : 0.62}
      />
    </mesh>
  );
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
  const devices = useMemo(
    () => topology?.devices.filter((d) => d.device_id !== 'internet' && d.device_id !== 'gateway') ?? [],
    [topology],
  );

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
  const activeClasses = useMemo(() => {
    const classes = new Map<string, TrafficClass>();
    for (const flow of flows) {
      if (flow.packets_per_second > 0) classes.set(flow.device_id, flow.traffic_class);
    }
    return classes;
  }, [flows]);

  const deviceRails = useMemo(
    () =>
      devices.map((device) => {
        const endpoint = devicePosition(device.scene_x, device.scene_z);
        return {
          deviceId: device.device_id,
          path: [
            new THREE.Vector3(endpoint.x, 0.12, endpoint.z),
            new THREE.Vector3(endpoint.x * 0.55, 0.1, endpoint.z * 0.55),
            new THREE.Vector3(0, 0.1, 0),
          ],
        };
      }),
    [devices],
  );

  return (
    <>
      <ambientLight intensity={0.55} />
      <directionalLight position={[6, 11, 5]} intensity={0.9} castShadow />
      <directionalLight position={[-7, 6, -4]} intensity={0.45} color="#4fcbd0" />
      <pointLight position={[0, 3.2, 0]} intensity={18} distance={9} color="#c69a5b" />

      <CameraRig animate={animate} />
      <RoomBackdrop />
      <GroundPlane />

      {deviceRails.map((rail) => {
        const activeClass = activeClasses.get(rail.deviceId);
        return (
          <NetworkRail
            key={`rail-${rail.deviceId}`}
            path={rail.path}
            active={Boolean(activeClass)}
            color={activeClass ? CLASS_COLOR[activeClass] : '#42615b'}
          />
        );
      })}
      <NetworkRail
        path={[
          new THREE.Vector3(0, INTERNET.y - 0.55, INTERNET.z),
          new THREE.Vector3(0, 2.4, -0.45),
          new THREE.Vector3(0, 0.12, 0),
        ]}
        active={flows.some((flow) => flow.packets_per_second > 0)}
        color="#58c9d0"
      />

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
        camera={{ position: [0, 10.8, 15.6], fov: 36 }}
        // Pausing the render loop while the tab is hidden keeps a background
        // tab from burning frames. Experiment timing is unaffected: the
        // simulator advances on the server.
        frameloop={animate ? 'always' : 'demand'}
        onCreated={({ gl }) => {
          gl.setClearColor('#081c1d', 0);
          gl.toneMapping = THREE.ACESFilmicToneMapping;
          gl.toneMappingExposure = 1.12;
          gl.shadowMap.type = THREE.PCFSoftShadowMap;
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
