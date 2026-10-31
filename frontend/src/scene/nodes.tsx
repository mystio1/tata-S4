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
  const coreRef = useRef<THREE.Group>(null);

  useFrame((state, delta) => {
    if (!ringRef.current) return;
    if (animate) {
      ringRef.current.rotation.z += delta * 0.25;
    }
    const base = shaping ? 1.0 : 0.72;
    const breathe = animate ? Math.sin(state.clock.elapsedTime * 1.6) * 0.04 : 0;
    ringRef.current.scale.setScalar(base + breathe + (pulse ? 0.16 : 0));
    if (coreRef.current && animate) {
      coreRef.current.rotation.y += delta * 0.08;
      coreRef.current.position.y = 1.06 + Math.sin(state.clock.elapsedTime * 1.3) * 0.035;
    }
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
      <mesh position={[0, 0.43, 0]} castShadow>
        <cylinderGeometry args={[0.99, 0.99, 0.12, 40]} />
        <meshStandardMaterial color="#273b37" roughness={0.32} metalness={0.78} />
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
      <mesh position={[0, 0.22, 0.89]}>
        <boxGeometry args={[0.72, 0.1, 0.035]} />
        <meshStandardMaterial color="#081817" roughness={0.25} metalness={0.5} />
      </mesh>
      {[-0.22, 0, 0.22].map((x) => (
        <group key={x} position={[x, 0.22, 0.92]}>
          <mesh>
            <boxGeometry args={[0.12, 0.035, 0.025]} />
            <meshStandardMaterial
              color={THEME.success}
              emissive={THEME.success}
              emissiveIntensity={0.9}
              toneMapped={false}
            />
          </mesh>
        </group>
      ))}
      {[-0.58, 0.58].map((x) => (
        <group key={x} position={[x, 0.8, -0.3]} rotation={[0, 0, x < 0 ? -0.1 : 0.1]}>
          <mesh position={[0, 0.52, 0]} castShadow>
            <cylinderGeometry args={[0.035, 0.05, 1.05, 10]} />
            <meshStandardMaterial color="#424c46" roughness={0.3} metalness={0.8} />
          </mesh>
          <mesh position={[0, 1.06, 0]}>
            <sphereGeometry args={[0.075, 12, 8]} />
            <meshStandardMaterial
              color={THEME.bronze}
              emissive={THEME.bronze}
              emissiveIntensity={0.6}
            />
          </mesh>
        </group>
      ))}
      <group ref={coreRef} position={[0, 1.06, 0]}>
        <mesh>
          <cylinderGeometry args={[0.26, 0.3, 0.62, 24, 1, true]} />
          <meshBasicMaterial
            color={shaping ? THEME.info : '#62807b'}
            transparent
            opacity={0.28}
            side={THREE.DoubleSide}
          />
        </mesh>
        {[0.25, 0.45].map((y) => (
          <mesh key={y} position={[0, y - 0.31, 0]}>
            <torusGeometry args={[0.29, 0.018, 8, 32]} />
            <meshBasicMaterial color={accent} transparent opacity={0.72} />
          </mesh>
        ))}
      </group>
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
  const orbitRef = useRef<THREE.Group>(null);
  useFrame((_, delta) => {
    if (ref.current && animate) ref.current.rotation.y += delta * 0.12;
    if (orbitRef.current && animate) orbitRef.current.rotation.y -= delta * 0.16;
  });

  return (
    <group position={position}>
      <mesh ref={ref}>
        <sphereGeometry args={[0.78, 24, 18]} />
        <meshStandardMaterial color="#113438" roughness={0.28} metalness={0.58} />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.81, 24, 18]} />
        <meshBasicMaterial
          color={congested ? THEME.critical : THEME.info}
          transparent
          opacity={0.34}
          wireframe
        />
      </mesh>
      <mesh rotation={[Math.PI / 2.4, 0.3, 0]}>
        <torusGeometry args={[0.93, 0.024, 8, 64]} />
        <meshBasicMaterial color={congested ? THEME.critical : THEME.info} transparent opacity={0.8} />
      </mesh>
      <group ref={orbitRef}>
        <mesh rotation={[0.4, 0, 0.9]}>
          <torusGeometry args={[1.06, 0.012, 6, 72]} />
          <meshBasicMaterial color="#c69a5b" transparent opacity={0.62} />
        </mesh>
      </group>
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
  LAPTOP: [1.16, 0.82, 0.78],
  GAMING_CONSOLE: [1.05, 0.34, 0.7],
  DESKTOP: [1.38, 1.25, 0.62],
  SMART_TV: [1.32, 0.92, 0.2],
  SMARTPHONE: [0.38, 0.72, 0.12],
};

export interface DeviceFlowSummary {
  trafficClass: TrafficClass;
  downloadMbps: number | null;
  uploadMbps: number | null;
  rttMs: number | null;
  muted: boolean;
}

function DeviceModel({ kind, accent }: { kind: DeviceKind; accent: string }) {
  const chassis = '#273532';
  const screen = '#10282a';

  if (kind === 'LAPTOP') {
    return (
      <group>
        <mesh position={[0, 0.15, 0]} castShadow>
          <boxGeometry args={[1.14, 0.12, 0.76]} />
          <meshStandardMaterial color={chassis} roughness={0.32} metalness={0.72} />
        </mesh>
        <mesh position={[0, 0.22, 0.07]}>
          <boxGeometry args={[0.78, 0.012, 0.38]} />
          <meshStandardMaterial color="#43504a" roughness={0.7} metalness={0.35} />
        </mesh>
        <mesh position={[0, 0.65, -0.29]} rotation={[-0.12, 0, 0]} castShadow>
          <boxGeometry args={[1.05, 0.78, 0.075]} />
          <meshStandardMaterial color="#343c37" roughness={0.28} metalness={0.72} />
        </mesh>
        <mesh position={[0, 0.65, -0.247]} rotation={[-0.12, 0, 0]}>
          <planeGeometry args={[0.94, 0.65]} />
          <meshStandardMaterial
            color={screen}
            emissive={accent}
            emissiveIntensity={0.72}
            roughness={0.22}
            metalness={0.18}
          />
        </mesh>
        <mesh position={[0, 0.266, -0.27]}>
          <cylinderGeometry args={[0.025, 0.025, 0.55, 8]} />
          <meshStandardMaterial color="#767264" metalness={0.8} roughness={0.28} />
        </mesh>
      </group>
    );
  }

  if (kind === 'GAMING_CONSOLE') {
    return (
      <group>
        <mesh position={[0, 0.15, 0]} castShadow>
          <boxGeometry args={[0.98, 0.22, 0.62]} />
          <meshStandardMaterial color={chassis} roughness={0.38} metalness={0.66} />
        </mesh>
        <mesh position={[0, 0.268, 0]}>
          <boxGeometry args={[0.74, 0.012, 0.44]} />
          <meshStandardMaterial color="#111b1b" roughness={0.75} />
        </mesh>
        {[-0.28, -0.14, 0, 0.14, 0.28].map((x) => (
          <mesh key={x} position={[x, 0.278, -0.02]}>
            <boxGeometry args={[0.012, 0.012, 0.32]} />
            <meshStandardMaterial color="#68736a" metalness={0.6} roughness={0.4} />
          </mesh>
        ))}
        <mesh position={[0.36, 0.16, 0.315]}>
          <boxGeometry args={[0.12, 0.035, 0.018]} />
          <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={1.4} />
        </mesh>
      </group>
    );
  }

  if (kind === 'DESKTOP') {
    return (
      <group>
        <mesh position={[-0.18, 0.75, -0.08]} castShadow>
          <boxGeometry args={[0.98, 0.68, 0.1]} />
          <meshStandardMaterial color="#343d38" roughness={0.34} metalness={0.62} />
        </mesh>
        <mesh position={[-0.18, 0.75, -0.022]}>
          <planeGeometry args={[0.86, 0.55]} />
          <meshStandardMaterial color={screen} emissive={accent} emissiveIntensity={0.56} />
        </mesh>
        <mesh position={[-0.18, 0.37, -0.08]}>
          <boxGeometry args={[0.12, 0.11, 0.1]} />
          <meshStandardMaterial color="#69716a" metalness={0.7} roughness={0.35} />
        </mesh>
        <mesh position={[-0.18, 0.31, 0.02]}>
          <boxGeometry args={[0.52, 0.045, 0.32]} />
          <meshStandardMaterial color="#4c544d" metalness={0.72} roughness={0.34} />
        </mesh>
        <mesh position={[0.52, 0.42, 0]} castShadow>
          <boxGeometry args={[0.36, 0.72, 0.52]} />
          <meshStandardMaterial color="#202b29" roughness={0.42} metalness={0.65} />
        </mesh>
        <mesh position={[0.52, 0.44, 0.266]}>
          <boxGeometry args={[0.2, 0.025, 0.015]} />
          <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={1.4} />
        </mesh>
      </group>
    );
  }

  if (kind === 'SMART_TV') {
    return (
      <group>
        <mesh position={[0, 0.57, 0]} castShadow>
          <boxGeometry args={[1.24, 0.76, 0.1]} />
          <meshStandardMaterial color="#343b36" roughness={0.28} metalness={0.7} />
        </mesh>
        <mesh position={[0, 0.57, 0.055]}>
          <planeGeometry args={[1.12, 0.64]} />
          <meshStandardMaterial color={screen} emissive={accent} emissiveIntensity={0.62} />
        </mesh>
        {[-0.38, 0.38].map((x) => (
          <mesh key={x} position={[x, 0.15, -0.015]} rotation={[0, 0, x < 0 ? -0.18 : 0.18]}>
            <boxGeometry args={[0.045, 0.34, 0.045]} />
            <meshStandardMaterial color="#555a51" metalness={0.72} roughness={0.34} />
          </mesh>
        ))}
      </group>
    );
  }

  if (kind === 'SMARTPHONE') {
    return (
      <group>
        <mesh position={[0, 0.39, 0]} castShadow>
          <boxGeometry args={[0.34, 0.68, 0.09]} />
          <meshStandardMaterial color="#343b37" roughness={0.25} metalness={0.78} />
        </mesh>
        <mesh position={[0, 0.39, 0.048]}>
          <planeGeometry args={[0.28, 0.58]} />
          <meshStandardMaterial color={screen} emissive={accent} emissiveIntensity={0.8} />
        </mesh>
        <mesh position={[0, 0.095, 0.051]}>
          <circleGeometry args={[0.018, 12]} />
          <meshBasicMaterial color="#aebdb6" />
        </mesh>
      </group>
    );
  }

  return (
    <mesh position={[0, 0.25, 0]} castShadow>
      <boxGeometry args={[0.8, 0.5, 0.8]} />
      <meshStandardMaterial color={chassis} emissive={accent} emissiveIntensity={0.2} />
    </mesh>
  );
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
    <group
      position={position}
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
      <mesh
        position={[0, size[1] / 2 + 0.08, 0]}
      >
        <boxGeometry args={size} />
        <meshBasicMaterial transparent opacity={0.001} depthWrite={false} />
      </mesh>
      <DeviceModel kind={kind} accent={accent} />
      {/* Pad under the device, tinted by its current traffic class */}
      <mesh position={[0, 0.02, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[Math.max(size[0], size[2]) * 0.82, 28]} />
        <meshBasicMaterial
          color={accent}
          transparent
          opacity={selected ? 0.34 : highlighted ? 0.25 : 0.15}
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
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.04, 0]} receiveShadow>
        <planeGeometry args={[19.2, 16.8]} />
        <meshStandardMaterial color="#10201e" roughness={0.72} metalness={0.18} />
      </mesh>
      <gridHelper args={[19.2, 24, '#76583b', '#29423d']} position={[0, 0.005, 0]} />
      {[
        { position: [0, 0.025, -8.35] as const, size: [18.8, 0.035, 0.08] as const },
        { position: [0, 0.025, 8.35] as const, size: [18.8, 0.035, 0.08] as const },
        { position: [-9.55, 0.025, 0] as const, size: [0.08, 0.035, 16.7] as const },
        { position: [9.55, 0.025, 0] as const, size: [0.08, 0.035, 16.7] as const },
      ].map((edge) => (
        <mesh key={edge.position.join(':')} position={edge.position}>
          <boxGeometry args={[edge.size[0], edge.size[1], edge.size[2]]} />
          <meshBasicMaterial color="#c69a5b" transparent opacity={0.6} />
        </mesh>
      ))}
    </group>
  );
}
