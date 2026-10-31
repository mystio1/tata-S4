/**
 * Scene objects: the gateway, the Internet peer, the device nodes and the
 * queue stack beside the gateway.
 *
 * Every visual state here is driven by a measurement or by a typed backend
 * event. Nothing animates a successful outcome because a button was pressed.
 */

import { useMemo, useRef } from 'react';
import { Html } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

import type { DeviceKind, TrafficClass } from '@/api/types';
import { CLASS_COLOR, CLASS_LABEL, THEME } from '@/lib/colors';
import { num } from '@/lib/format';

/** Number of blocks in the queue stack. Each block is ten percent of the buffer. */
export const QUEUE_BLOCKS = 10;

export function GatewayModel({
  shaping,
  animate,
  pulse,
}: {
  shaping: boolean;
  animate: boolean;
  pulse: boolean;
}) {
  const ringRef = useRef<THREE.Mesh>(null);

  useFrame((state, delta) => {
    if (!ringRef.current) return;
    if (animate) {
      ringRef.current.rotation.z += delta * 0.25;
    }
    const base = shaping ? 1.0 : 0.72;
    const breathe = animate ? Math.sin(state.clock.elapsedTime * 1.6) * 0.04 : 0;
    ringRef.current.scale.setScalar(base + breathe + (pulse ? 0.16 : 0));
  });

  const accent = shaping ? THEME.bronze : THEME.faint;

  return (
    <group position={[0, 0, 0]}>
      {/* Plinth */}
      <mesh position={[0, 0.1, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[1.25, 1.42, 0.2, 40]} />
        <meshStandardMaterial color="#163a37" roughness={0.6} metalness={0.3} />
      </mesh>
      {/* Body */}
      <mesh position={[0, 0.42, 0]} castShadow>
        <cylinderGeometry args={[0.95, 1.1, 0.46, 40]} />
        <meshStandardMaterial color="#1c3331" roughness={0.45} metalness={0.45} />
      </mesh>
      {/* Accent band: bronze once shaping is installed, grey while unshaped */}
      <mesh position={[0, 0.66, 0]}>
        <cylinderGeometry args={[0.97, 0.97, 0.05, 40]} />
        <meshStandardMaterial
          color={accent}
          emissive={accent}
          emissiveIntensity={shaping ? 0.9 : 0.25}
          roughness={0.3}
          metalness={0.7}
        />
      </mesh>
      {/* Rotating status ring */}
      <mesh ref={ringRef} position={[0, 0.03, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[1.5, 1.62, 56]} />
        <meshBasicMaterial
          color={accent}
          transparent
          opacity={shaping ? 0.5 : 0.22}
          side={THREE.DoubleSide}
        />
      </mesh>
      <Html position={[0, -0.3, 1.5]} center distanceFactor={13} zIndexRange={[20, 0]}>
        <div className="scene-label" style={{ textAlign: 'center' }}>
          <div className="scene-label__name" style={{ color: THEME.bronze }}>
            QoSense Gateway
          </div>
          <div className="scene-label__class" style={{ color: THEME.muted }}>
            {shaping ? 'Shaping installed' : 'No shaping installed'}
          </div>
        </div>
      </Html>
    </group>
  );
}

export function InternetNode({
  position,
  congested,
  animate,
}: {
  position: [number, number, number];
  congested: boolean;
  animate: boolean;
}) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame((_, delta) => {
    if (ref.current && animate) ref.current.rotation.y += delta * 0.12;
  });

  return (
    <group position={position}>
      <mesh ref={ref}>
        <sphereGeometry args={[0.78, 24, 18]} />
        <meshStandardMaterial
          color="#13403f"
          emissive={congested ? THEME.critical : THEME.info}
          emissiveIntensity={congested ? 0.42 : 0.2}
          roughness={0.5}
          metalness={0.3}
          wireframe
        />
      </mesh>
      <Html position={[0, 1.15, 0]} center distanceFactor={13} zIndexRange={[20, 0]}>
        <div className="scene-label" style={{ textAlign: 'center' }}>
          <div className="scene-label__name">Internet</div>
          <div className="scene-label__class" style={{ color: THEME.muted }}>
            Wide area network
          </div>
        </div>
      </Html>
    </group>
  );
}

const DEVICE_SHAPE: Record<DeviceKind, [number, number, number]> = {
  GATEWAY: [0.8, 0.5, 0.8],
  INTERNET: [0.8, 0.8, 0.8],
  LAPTOP: [0.95, 0.12, 0.66],
  GAMING_CONSOLE: [0.78, 0.26, 0.6],
  DESKTOP: [0.5, 0.82, 0.5],
  SMART_TV: [1.12, 0.66, 0.1],
  SMARTPHONE: [0.3, 0.56, 0.06],
};

export interface DeviceFlowSummary {
  trafficClass: TrafficClass;
  downloadMbps: number | null;
  uploadMbps: number | null;
  rttMs: number | null;
  muted: boolean;
}

export function DeviceNode({
  position,
  label,
  kind,
  summary,
  selected,
  highlighted,
  onSelect,
}: {
  position: [number, number, number];
  label: string;
  kind: DeviceKind;
  summary: DeviceFlowSummary | null;
  selected: boolean;
  highlighted: boolean;
  onSelect: () => void;
}) {
  const size = DEVICE_SHAPE[kind] ?? [0.7, 0.4, 0.7];
  const accent = summary
    ? summary.muted
      ? CLASS_COLOR.UNKNOWN
      : CLASS_COLOR[summary.trafficClass]
    : THEME.faint;

  return (
    <group position={position}>
      <mesh
        position={[0, size[1] / 2 + 0.08, 0]}
        castShadow
        onClick={(event) => {
          event.stopPropagation();
          onSelect();
        }}
        onPointerOver={(event) => {
          event.stopPropagation();
          document.body.style.cursor = 'pointer';
        }}
        onPointerOut={() => {
          document.body.style.cursor = 'default';
        }}
      >
        <boxGeometry args={size} />
        <meshStandardMaterial
          color="#1b3330"
          emissive={accent}
          emissiveIntensity={highlighted ? 0.55 : selected ? 0.35 : 0.14}
          roughness={0.5}
          metalness={0.35}
        />
      </mesh>
      {/* Pad under the device, tinted by its current traffic class */}
      <mesh position={[0, 0.02, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[Math.max(size[0], size[2]) * 0.82, 28]} />
        <meshBasicMaterial
          color={accent}
          transparent
          opacity={selected ? 0.3 : 0.12}
        />
      </mesh>
      <Html
        position={[0, size[1] + 0.52, 0]}
        center
        distanceFactor={13}
        zIndexRange={[20, 0]}
      >
        <div
          className="scene-label"
          style={{ borderColor: selected ? THEME.bronze : undefined }}
        >
          <div className="scene-label__name">{label}</div>
          {summary ? (
            <>
              <div className="scene-label__class" style={{ color: accent }}>
                {summary.muted ? 'Unknown' : CLASS_LABEL[summary.trafficClass]}
              </div>
              <div className="scene-label__metric">
                ↓ {num(summary.downloadMbps, 1)} ↑ {num(summary.uploadMbps, 1)} Mbps
              </div>
              <div className="scene-label__metric">
                Flow delay {num(summary.rttMs, 0)} ms
              </div>
            </>
          ) : (
            <div className="scene-label__class" style={{ color: THEME.faint }}>
              Idle
            </div>
          )}
        </div>
      </Html>
    </group>
  );
}

/**
 * The queue beside the gateway.
 *
 * Block count comes from measured queue occupancy, so the stack grows only
 * because the backend reported a deeper queue. The bufferbloat label appears
 * only when the backend's own congestion state says so, never because the
 * stack looks tall.
 */
export function QueueStack({
  position,
  occupancyPercent,
  queuePackets,
  queueDelayMs,
  bufferbloat,
  animate,
}: {
  position: [number, number, number];
  occupancyPercent: number | null;
  queuePackets: number | null;
  queueDelayMs: number | null;
  bufferbloat: boolean;
  animate: boolean;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const occupancy = occupancyPercent ?? 0;
  const filled = Math.round((occupancy / 100) * QUEUE_BLOCKS);

  const color = useMemo(() => {
    if (occupancy >= 70) return THEME.critical;
    if (occupancy >= 35) return THEME.warning;
    return THEME.success;
  }, [occupancy]);

  useFrame((state) => {
    if (!groupRef.current || !animate || !bufferbloat) return;
    // A slow pulse only while the backend reports bufferbloat.
    const pulse = 1 + Math.sin(state.clock.elapsedTime * 3) * 0.05;
    groupRef.current.scale.set(pulse, 1, pulse);
  });

  return (
    <group position={position} ref={groupRef}>
      {Array.from({ length: QUEUE_BLOCKS }, (_, index) => {
        const isFilled = index < filled;
        return (
          <mesh key={index} position={[0, 0.1 + index * 0.17, 0]}>
            <boxGeometry args={[0.52, 0.13, 0.52]} />
            <meshStandardMaterial
              color={isFilled ? color : '#15302e'}
              emissive={isFilled ? color : '#000000'}
              emissiveIntensity={isFilled ? 0.5 : 0}
              roughness={0.5}
              metalness={0.2}
              transparent
              opacity={isFilled ? 1 : 0.35}
            />
          </mesh>
        );
      })}
      <Html position={[0, QUEUE_BLOCKS * 0.17 + 0.45, 0]} center distanceFactor={13}>
        <div className="scene-label" style={{ textAlign: 'center' }}>
          <div className="scene-label__name">Download queue</div>
          <div className="scene-label__metric" style={{ color }}>
            {num(occupancyPercent, 0)}% · {queuePackets ?? '—'} pkt
          </div>
          <div className="scene-label__metric">+{num(queueDelayMs, 1)} ms delay</div>
          {bufferbloat && (
            <div className="scene-label__class" style={{ color: THEME.critical }}>
              Bufferbloat measured
            </div>
          )}
        </div>
      </Html>
    </group>
  );
}

export function GroundPlane() {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.02, 0]} receiveShadow>
        <circleGeometry args={[11, 56]} />
        <meshStandardMaterial color="#0c2524" roughness={0.95} metalness={0.05} />
      </mesh>
      <gridHelper args={[22, 22, '#1b3b38', '#143230']} position={[0, 0, 0]} />
    </group>
  );
}
