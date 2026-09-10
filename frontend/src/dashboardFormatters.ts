/**
 * Format a fractional rate as a percentage for display.
 */
export function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}

/**
 * Build an encoded stock-detail route and optional name query parameter.
 */
export function stockDetailPath(symbol: string, name?: string) {
  const path = `/stocks/${encodeURIComponent(symbol)}`;
  const params = new URLSearchParams();
  if (name) params.set("name", name);
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

/**
 * Render an empirical fractional rate to one decimal place; missing values stay visibly
 * unavailable.
 */
export function formatEmpiricalRate(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : `${(value * 100).toFixed(1)}%`;
}

/**
 * Convert a currency amount to hundred-million units for the compact dashboard display.
 */
export function formatAmount(value: number) {
  return `${(value / 100_000_000).toFixed(1)} 亿`;
}

/**
 * Choose ten-thousand or hundred-million units for a signed flow amount; preserve missingness.
 */
export function formatNetAmount(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "暂无";
  }
  const absolute = Math.abs(value);
  if (absolute >= 100_000_000) {
    return `${formatSigned(value / 100_000_000, 2)} 亿`;
  }
  return `${formatSigned(value / 10_000, 0)} 万`;
}

/**
 * Choose the positive/negative CSS tone, leaving missing or zero values neutral.
 */
export function numberTone(value: number | null | undefined) {
  if (value === null || value === undefined || value === 0) {
    return "";
  }
  return value > 0 ? "positive" : "negative";
}

/**
 * Render an already-percent-valued change with a sign, or the unavailable label.
 */
export function formatOptionalPercent(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : `${formatSigned(value, 1)}%`;
}

/**
 * Round a numeric value and prefix positive values with a plus sign.
 */
export function formatSigned(value: number, decimals = 1) {
  return value > 0 ? `+${value.toFixed(decimals)}` : value.toFixed(decimals);
}
