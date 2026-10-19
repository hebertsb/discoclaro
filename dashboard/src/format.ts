const GIB = 1024 ** 3;

export function formatGb(bytes: number): string {
  return `${(bytes / GIB).toLocaleString("es", { maximumFractionDigits: 1 })} GB`;
}

export function formatPercent(value: number): string {
  return `${value.toLocaleString("es", { maximumFractionDigits: 1 })} %`;
}

export function formatDays(days: number): string {
  const rounded = Math.round(days);
  return rounded === 1 ? "1 día" : `${rounded.toLocaleString("es")} días`;
}
