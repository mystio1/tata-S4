/**
 * Semantic colours.
 *
 * Traffic class colours are fixed by `docs/UI_SPEC.md` and mean the same thing
 * on every page and in the three-dimensional scene. Bronze is the brand accent
 * and is never substituted for a class colour.
 */

import type {
  CongestionState,
  DataMode,
  DataQuality,
  EventSeverity,
  ModelState,
  TrafficClass,
} from '@/api/types';

export const CLASS_COLOR: Record<TrafficClass, string> = {
  INTERACTIVE: '#4fcbd0',
  GAMING_LIKE: '#a78bda',
  STREAMING_LIKE: '#e79a47',
  BULK: '#5796c8',
  WEB_VOICE: '#58b99d',
  UNKNOWN: '#7b8d87',
};

export const CLASS_LABEL: Record<TrafficClass, string> = {
  INTERACTIVE: 'Interactive',
  GAMING_LIKE: 'Gaming',
  STREAMING_LIKE: 'Streaming',
  BULK: 'Bulk download',
  WEB_VOICE: 'Web / voice',
  UNKNOWN: 'Unknown',
};

export const CLASS_ORDER: TrafficClass[] = [
  'INTERACTIVE',
  'GAMING_LIKE',
  'STREAMING_LIKE',
  'BULK',
  'WEB_VOICE',
  'UNKNOWN',
];

export const THEME = {
  bronze: '#c69a5b',
  champagne: '#e9e2d5',
  success: '#58b99d',
  warning: '#e58a45',
  critical: '#d85b52',
  info: '#4fcbd0',
  muted: '#aab9b2',
  faint: '#7b8d87',
  grid: '#1f3f3c',
  surface: '#12302d',
} as const;

export function congestionColor(state: CongestionState): string {
  switch (state) {
    case 'CLEAR':
      return THEME.success;
    case 'BUILDING':
      return THEME.warning;
    case 'CONGESTED':
    case 'BUFFERBLOAT':
      return THEME.critical;
    default:
      return THEME.faint;
  }
}

export function severityColor(severity: EventSeverity): string {
  switch (severity) {
    case 'SUCCESS':
      return THEME.success;
    case 'WARNING':
      return THEME.warning;
    case 'CRITICAL':
      return THEME.critical;
    default:
      return THEME.info;
  }
}

export function qualityTone(quality: DataQuality): 'success' | 'warning' | 'muted' {
  if (quality === 'GOOD') return 'success';
  if (quality === 'LOW_CONFIDENCE' || quality === 'STALE') return 'warning';
  return 'muted';
}

export function modeBadgeClass(mode: DataMode): string {
  if (mode === 'LIVE') return 'badge badge--live';
  if (mode === 'REPLAY') return 'badge badge--replay';
  return 'badge badge--sim';
}

export function modelStateTone(
  state: ModelState,
): 'success' | 'warning' | 'critical' | 'muted' {
  switch (state) {
    case 'READY':
      return 'success';
    case 'TRAINING':
      return 'warning';
    case 'FAILED':
      return 'critical';
    default:
      return 'muted';
  }
}
