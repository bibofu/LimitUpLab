const COUNT_FORMAT = new Intl.NumberFormat("zh-CN");

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return COUNT_FORMAT.format(Math.round(value));
}

/** The backend returns ratios in [0, 1]; display them as percentages. */
export function formatPercent(
  value: number | null | undefined,
  digits = 1,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value === 0) return "$0";
  return `$${value.toFixed(value < 0.01 ? 4 : 2)}`;
}

export function formatLatency(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value >= 1000) return `${(value / 1000).toFixed(2)}s`;
  return `${Math.round(value)}ms`;
}

export function formatDuration(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value < 60) return `${Math.round(value)}秒`;
  if (value < 3600) return `${Math.round(value / 60)}分钟`;
  if (value < 86400) return `${(value / 3600).toFixed(1)}小时`;
  return `${(value / 86400).toFixed(1)}天`;
}

/** Width for the simple CSS trend bars, bounded to 0–100. */
export function barWidth(value: number, maximum: number): number {
  if (!Number.isFinite(value) || !Number.isFinite(maximum) || maximum <= 0) return 0;
  return Math.max(0, Math.min(100, (value / maximum) * 100));
}
