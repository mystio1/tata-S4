/**
 * Formatting helpers.
 *
 * The single rule here: a value that was not measured renders as an em dash,
 * never as zero. `docs/BUILD_SPEC.md` forbids replacing an unavailable metric
 * with an attractive example value, and that starts at the formatter.
 */

export const NOT_MEASURED = '—';

export function num(
  value: number | null | undefined,
  digits = 1,
  fallback = NOT_MEASURED,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  return value.toFixed(digits);
}

export function mbps(value: number | null | undefined, digits = 1): string {
  return num(value, digits);
}

export function bytesPerSecondToMbps(bytesPerSecond: number): number {
  return (bytesPerSecond * 8) / 1_000_000;
}

export function percent(value: number | null | undefined, digits = 1): string {
  return num(value, digits);
}

export function integer(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NOT_MEASURED;
  return Math.round(value).toLocaleString('en-GB');
}

export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return NOT_MEASURED;
  if (value < 1024) return `${Math.round(value)} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return NOT_MEASURED;
  if (value < 60) return `${value.toFixed(0)} s`;
  const minutes = Math.floor(value / 60);
  return `${minutes} m ${Math.round(value % 60)} s`;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return NOT_MEASURED;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return NOT_MEASURED;
  return date.toLocaleTimeString('en-GB', { hour12: false });
}

/** Turn SCREAMING_SNAKE_CASE into readable words without inventing meaning. */
export function humanise(value: string): string {
  return value
    .toLowerCase()
    .replace(/_/g, ' ')
    .replace(/^\w/, (c) => c.toUpperCase());
}

export function elapsedLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return NOT_MEASURED;
  const total = Math.floor(value);
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}
