import type { PlanOut, SummaryOut, TreeOut } from "./types";

// Rutas relativas a propósito: en producción (T3.8) la misma API sirve el
// HTML/JS construido, un solo origen, sin CORS; en desarrollo, el proxy de
// `vite.config.ts` reenvía estas mismas rutas a la API real en otro puerto.
const API_BASE_URL = "";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, body.detail ?? response.statusText);
  }
  return (await response.json()) as T;
}

export function fetchSummary(): Promise<SummaryOut> {
  return getJson<SummaryOut>("/summary");
}

export function fetchPlan(): Promise<PlanOut> {
  return getJson<PlanOut>("/plan");
}

export function fetchTree(path: string | null): Promise<TreeOut> {
  const query = path === null ? "" : `?path=${encodeURIComponent(path)}`;
  return getJson<TreeOut>(`/tree${query}`);
}
