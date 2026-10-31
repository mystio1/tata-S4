/**
 * Animated packet flow along one link.
 *
 * What each visual property means, and what it deliberately does not mean:
 *
 * * **Particle count** is driven by the flow's measured packet rate, through
 *   the documented aggregation in `particlesForPacketRate`. One particle
 *   stands for many packets; there is never one object per real packet.
 * * **Brightness and thickness** follow the flow's measured throughput.
 * * **Travel speed is fixed.** It is a visual transit time, not a measurement.
 *   Tying particle speed to throughput would suggest that a faster link moves
 *   individual packets faster, which is not what throughput means. The speed
 *   constant below is the whole of that decision.
 * * **Direction** is physical: upload particles run device to gateway to
 *   Internet, download particles run the other way.
 *
 * All particles for one flow live in a single instanced mesh.
 */

import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

import type { TrafficClass } from '@/api/types';
import { CLASS_COLOR } from '@/lib/colors';

/** Seconds for one particle to traverse the whole path. A visual constant. */
export const VISUAL_TRANSIT_SECONDS = 2.6;
/** Packets per second represented by one particle. */
export const PACKETS_PER_PARTICLE = 90;
/** Never draw more than this many particles for one flow. */
export const MAX_PARTICLES_PER_FLOW = 26;

/**
 * How many particles represent a measured packet rate.
 *
 * A documented aggregation, not a per-packet rendering: at
 * `PACKETS_PER_PARTICLE` packets per second per particle, a flow sending
 * 4 500 packets per second draws 26 particles, not 4 500 objects.
 */
export function particlesForPacketRate(packetsPerSecond: number): number {
  if (packetsPerSecond <= 0) return 0;
  return Math.max(1, Math.min(MAX_PARTICLES_PER_FLOW, Math.round(packetsPerSecond / PACKETS_PER_PARTICLE)));
}

const PARTICLE_GEOMETRY = new THREE.SphereGeometry(0.075, 8, 8);

export interface PacketStreamProps {
  /** Ordered waypoints. Particles travel from the first to the last. */
  path: THREE.Vector3[];
  trafficClass: TrafficClass;
  packetsPerSecond: number;
  /** 0 to 1, from measured throughput against the busiest flow on screen. */
  intensity: number;
  /** Dim the stream when the controller does not know what this flow is. */
  muted?: boolean;
  animate: boolean;
  /** Briefly brighten the stream, for example when it is reclassified. */
  highlight?: boolean;
}

export function PacketStream({
  path,
  trafficClass,
  packetsPerSecond,
  intensity,
  muted = false,
  animate,
  highlight = false,
}: PacketStreamProps) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const phaseRef = useRef(Math.random());
  const dummy = useMemo(() => new THREE.Object3D(), []);

  const curve = useMemo(() => new THREE.CatmullRomCurve3(path, false, 'catmullrom', 0.1), [path]);
  const count = particlesForPacketRate(packetsPerSecond);
  const color = muted ? CLASS_COLOR.UNKNOWN : CLASS_COLOR[trafficClass];

  const tubeGeometry = useMemo(
    () => new THREE.TubeGeometry(curve, 40, 0.012 + intensity * 0.022, 6, false),
    [curve, intensity],
  );

  useFrame((_, delta) => {
    const mesh = meshRef.current;
    if (!mesh) return;

    if (animate) {
      phaseRef.current = (phaseRef.current + delta / VISUAL_TRANSIT_SECONDS) % 1;
    }

    for (let index = 0; index < count; index += 1) {
      // Spread the particles evenly along the path so density reads as rate.
      const t = (phaseRef.current + index / count) % 1;
      const point = curve.getPointAt(t);
      dummy.position.copy(point);
      const scale = 0.72 + intensity * 0.6;
      dummy.scale.setScalar(muted ? scale * 0.68 : scale);
      dummy.updateMatrix();
      mesh.setMatrixAt(index, dummy.matrix);
    }
    mesh.count = count;
    mesh.instanceMatrix.needsUpdate = true;
  });

  if (count === 0) {
    return null;
  }

  const emissiveStrength = (muted ? 0.3 : 0.75) + intensity * 0.9 + (highlight ? 1.1 : 0);

  return (
    <group>
      <mesh geometry={tubeGeometry}>
        <meshBasicMaterial
          color={color}
          transparent
          opacity={muted ? 0.12 : 0.16 + intensity * 0.3}
          depthWrite={false}
        />
      </mesh>
      <instancedMesh
        ref={meshRef}
        args={[PARTICLE_GEOMETRY, undefined, MAX_PARTICLES_PER_FLOW]}
        frustumCulled={false}
      >
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={emissiveStrength}
          roughness={0.35}
          metalness={0.1}
          transparent
          opacity={muted ? 0.55 : 1}
        />
      </instancedMesh>
    </group>
  );
}
