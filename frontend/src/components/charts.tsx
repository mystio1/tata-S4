/**
 * Charts.
 *
 * One charting library is used throughout, as `CLAUDE.md` requires. Every
 * series is fed from the metrics snapshots in the store, so a chart cannot
 * disagree with the scene or the panels beside it.
 *
 * A gap in a line means the value was not measured. Nothing is interpolated
 * across a missing sample.
 */

import { useMemo } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { MetricsSnapshot } from '@/api/types';
import { THEME } from '@/lib/colors';
import { EmptyState } from './primitives';

const AXIS = {
  stroke: THEME.faint,
  fontSize: 10,
  tickLine: false,
  axisLine: { stroke: THEME.grid },
} as const;

const TOOLTIP_STYLE = {
  background: '#0b2323',
  border: `1px solid ${THEME.grid}`,
  borderRadius: 6,
  fontSize: 12,
} as const;

export interface SeriesPoint {
  t: number;
  [key: string]: number | null;
}

function toPoints(
  series: MetricsSnapshot[],
  pick: (s: MetricsSnapshot) => Record<string, number | null>,
): SeriesPoint[] {
  return series.map((snapshot) => ({
    t: Math.round(snapshot.elapsed_seconds * 10) / 10,
    ...pick(snapshot),
  }));
}

function ChartFrame({
  data,
  children,
  height,
}: {
  data: unknown[];
  children: React.ReactElement;
  height: number;
}) {
  if (data.length === 0) {
    return (
      <div style={{ height }}>
        <EmptyState
          title="No samples yet."
          hint="Start an experiment to begin collecting measurements."
        />
      </div>
    );
  }
  return (
    <div style={{ height, width: '100%' }}>
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

export function ThroughputChart({
  series,
  height = 150,
}: {
  series: MetricsSnapshot[];
  height?: number;
}) {
  const data = useMemo(
    () =>
      toPoints(series, (s) => ({
        download: s.download_mbps,
        upload: s.upload_mbps,
        capacity: s.usable_capacity_mbps,
      })),
    [series],
  );

  return (
    <ChartFrame data={data} height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
        <defs>
          <linearGradient id="grad-download" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={THEME.info} stopOpacity={0.45} />
            <stop offset="100%" stopColor={THEME.info} stopOpacity={0.02} />
          </linearGradient>
          <linearGradient id="grad-upload" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#a78bda" stopOpacity={0.4} />
            <stop offset="100%" stopColor="#a78bda" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={THEME.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="t" {...AXIS} unit="s" minTickGap={28} />
        <YAxis {...AXIS} width={44} unit="M" />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelFormatter={(value) => `t = ${value} s`}
          formatter={(value: number, name: string) => [`${value?.toFixed(2)} Mbps`, name]}
        />
        <Legend wrapperStyle={{ fontSize: 10, color: THEME.muted }} iconSize={8} />
        <Area
          type="monotone"
          dataKey="download"
          name="Download"
          stroke={THEME.info}
          fill="url(#grad-download)"
          strokeWidth={1.6}
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Area
          type="monotone"
          dataKey="upload"
          name="Upload"
          stroke="#a78bda"
          fill="url(#grad-upload)"
          strokeWidth={1.6}
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Line
          type="monotone"
          dataKey="capacity"
          name="Usable capacity estimate"
          stroke={THEME.bronze}
          strokeWidth={1.3}
          strokeDasharray="4 3"
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
      </AreaChart>
    </ChartFrame>
  );
}

export function DelayJitterChart({
  series,
  targetMs,
  height = 150,
}: {
  series: MetricsSnapshot[];
  targetMs?: number;
  height?: number;
}) {
  const data = useMemo(
    () =>
      toPoints(series, (s) => ({
        rtt: s.rtt_ms,
        added: s.added_delay_ms,
        jitter: s.jitter_ms,
      })),
    [series],
  );

  return (
    <ChartFrame data={data} height={height}>
      <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid stroke={THEME.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="t" {...AXIS} unit="s" minTickGap={28} />
        <YAxis {...AXIS} width={44} unit="ms" />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelFormatter={(value) => `t = ${value} s`}
          formatter={(value: number, name: string) => [`${value?.toFixed(2)} ms`, name]}
        />
        <Legend wrapperStyle={{ fontSize: 10, color: THEME.muted }} iconSize={8} />
        {targetMs !== undefined && (
          <ReferenceLine
            y={targetMs}
            stroke={THEME.warning}
            strokeDasharray="3 3"
            strokeOpacity={0.7}
            label={{
              value: `added-delay target ${targetMs} ms`,
              fill: THEME.warning,
              fontSize: 9,
              position: 'insideTopRight',
            }}
          />
        )}
        <Line
          type="monotone"
          dataKey="rtt"
          name="Round-trip time"
          stroke={THEME.warning}
          strokeWidth={1.6}
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Line
          type="monotone"
          dataKey="added"
          name="Added delay over baseline"
          stroke={THEME.critical}
          strokeWidth={1.4}
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Line
          type="monotone"
          dataKey="jitter"
          name="Jitter"
          stroke={THEME.success}
          strokeWidth={1.4}
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
      </LineChart>
    </ChartFrame>
  );
}

export function QueueLossChart({
  series,
  height = 150,
}: {
  series: MetricsSnapshot[];
  height?: number;
}) {
  const data = useMemo(
    () =>
      toPoints(series, (s) => ({
        occupancy: s.queue_occupancy_percent,
        loss: s.packet_loss_percent,
      })),
    [series],
  );

  return (
    <ChartFrame data={data} height={height}>
      <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid stroke={THEME.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="t" {...AXIS} unit="s" minTickGap={28} />
        <YAxis {...AXIS} width={44} yAxisId="left" unit="%" domain={[0, 100]} />
        <YAxis {...AXIS} width={36} yAxisId="right" orientation="right" unit="%" />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelFormatter={(value) => `t = ${value} s`}
          formatter={(value: number, name: string) => [`${value?.toFixed(2)} %`, name]}
        />
        <Legend wrapperStyle={{ fontSize: 10, color: THEME.muted }} iconSize={8} />
        <Line
          yAxisId="left"
          type="monotone"
          dataKey="occupancy"
          name="Queue occupancy"
          stroke={THEME.bronze}
          strokeWidth={1.6}
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        <Line
          yAxisId="right"
          type="monotone"
          dataKey="loss"
          name="Packet loss"
          stroke={THEME.critical}
          strokeWidth={1.4}
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
      </LineChart>
    </ChartFrame>
  );
}

/**
 * Arrival timeline for jitter.
 *
 * Ticks are evenly spaced when the measured delay variation is low and
 * irregularly spaced as it rises. Spacing is the only thing that encodes
 * jitter here: particle volume never does, because volume already means
 * packet rate elsewhere in the interface.
 */
export function JitterArrivalTimeline({
  jitterMs,
  rttMs,
  height = 44,
}: {
  jitterMs: number | null;
  rttMs: number | null;
  height?: number;
}) {
  const ticks = useMemo(() => {
    const count = 26;
    if (jitterMs === null) return [];
    // A deterministic offset pattern scaled by measured jitter, so the same
    // jitter always draws the same spacing.
    const spread = Math.min(1, jitterMs / 25);
    return Array.from({ length: count }, (_, index) => {
      const base = (index + 0.5) / count;
      const wobble = Math.sin(index * 2.399) * 0.5 * spread * (1 / count) * 2.2;
      return Math.min(0.99, Math.max(0.01, base + wobble));
    });
  }, [jitterMs]);

  if (jitterMs === null) {
    return (
      <div className="faint small" style={{ height }}>
        Delay variation not measured yet.
      </div>
    );
  }

  const tone = jitterMs > 12 ? THEME.critical : jitterMs > 5 ? THEME.warning : THEME.success;

  return (
    <div>
      <svg
        viewBox="0 0 100 10"
        preserveAspectRatio="none"
        style={{ width: '100%', height }}
        role="img"
        aria-label={`Packet arrival spacing at ${jitterMs.toFixed(2)} milliseconds of measured delay variation`}
      >
        <line x1={0} y1={5} x2={100} y2={5} stroke={THEME.grid} strokeWidth={0.4} />
        {ticks.map((position, index) => (
          <line
            key={index}
            x1={position * 100}
            y1={1.5}
            x2={position * 100}
            y2={8.5}
            stroke={tone}
            strokeWidth={0.7}
          />
        ))}
      </svg>
      <div className="small faint">
        Even spacing means low measured delay variation. Current jitter{' '}
        <span className="mono" style={{ color: tone }}>
          {jitterMs.toFixed(2)} ms
        </span>
        {rttMs !== null && (
          <>
            {' '}
            against a round-trip time of{' '}
            <span className="mono">{rttMs.toFixed(1)} ms</span>
          </>
        )}
        . Round-trip time is not one-way delay.
      </div>
    </div>
  );
}

/** A compact radial gauge for the health row. */
export function Gauge({
  label,
  value,
  unit,
  max,
  color,
  size = 72,
}: {
  label: string;
  value: number | null;
  unit: string;
  max: number;
  color: string;
  size?: number;
}) {
  const radius = size / 2 - 6;
  const circumference = 2 * Math.PI * radius;
  const ratio = value === null ? 0 : Math.min(1, Math.max(0, value / max));
  const measured = value !== null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
      <svg width={size} height={size} role="img" aria-label={`${label}: ${value ?? 'not measured'}`}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={THEME.grid}
          strokeWidth={5}
        />
        {measured && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={5}
            strokeLinecap="round"
            strokeDasharray={`${circumference * ratio} ${circumference}`}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        )}
        <text
          x="50%"
          y="48%"
          textAnchor="middle"
          fill={measured ? THEME.champagne : THEME.faint}
          fontSize={size / 4.4}
          fontFamily="monospace"
        >
          {measured ? value.toFixed(value < 10 ? 1 : 0) : '—'}
        </text>
        <text
          x="50%"
          y="66%"
          textAnchor="middle"
          fill={THEME.faint}
          fontSize={size / 7.5}
        >
          {unit}
        </text>
      </svg>
      <span className="small faint">{label}</span>
    </div>
  );
}
