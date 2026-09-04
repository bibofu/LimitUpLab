export function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}

export function stockDetailPath(symbol: string, tradeDate?: string, name?: string) {
  const path = `/stocks/${encodeURIComponent(symbol)}`;
  const params = new URLSearchParams();
  if (tradeDate) params.set("trade_date", tradeDate);
  if (name) params.set("name", name);
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

export function formatEmpiricalRate(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : `${(value * 100).toFixed(1)}%`;
}

export function formatAmount(value: number) {
  return `${(value / 100_000_000).toFixed(1)} 亿`;
}

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

export function numberTone(value: number | null | undefined) {
  if (value === null || value === undefined || value === 0) {
    return "";
  }
  return value > 0 ? "positive" : "negative";
}

export function formatOptionalPercent(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : `${formatSigned(value, 1)}%`;
}

export function formatSigned(value: number, decimals = 1) {
  return value > 0 ? `+${value.toFixed(decimals)}` : value.toFixed(decimals);
}
