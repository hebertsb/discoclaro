import type {
  BatchCleanOut,
  CategoriesDetailOut,
  CategoryTotalOut,
  CleanupHistoryOut,
  DevProjectsOverviewOut,
  DuplicatesOverviewOut,
  FootprintOut,
  GrowthOut,
  LimitsOverviewOut,
  MigrationAssistantOut,
  PhysicalDiskHealthOut,
  PlanOut,
  ProgramsOverviewOut,
  RecommendationOut,
  RecycleBinEmptyOut,
  RecycleBinStatusOut,
  ScanVolumeOut,
  ScheduleOverviewOut,
  SearchResultsOut,
  SnapshotOut,
  SummaryOut,
  TerminalCommandOut,
  TrashBatchOut,
  TreeOut,
  UninstallFlowOut,
  VhdxCompactOut,
  VhdxDiskOut,
} from "./types";

const API_BASE_URL = "";

// Caché de doble capa (RAM + sessionStorage/localStorage) con Stale-While-Revalidate:
// - Al abrir o recargar la página (F5 / Ctrl+F5), sirve al instante (0 ms) la
//   última respuesta guardada en localStorage para que no aparezcan esqueletos
//   grises esperando a los inspectores, y refresca en segundo plano.
const CLIENT_CACHE_TTL_MS = 45_000;
const STORAGE_PREFIX = "discoclaro.swr.v4:";
const responseCache = new Map<string, { at: number; data: unknown }>();
const inflightRequests = new Map<string, Promise<unknown>>();

const INSTANT_BOOTSTRAP_SEED: Record<string, unknown> = {};

function readPersistedCache(path: string): { at: number; data: unknown } | null {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + path);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { at: number; data: unknown };
    if (parsed && typeof parsed.at === "number" && "data" in parsed) {
      responseCache.set(path, parsed);
      return parsed;
    }
  } catch {
    // ignore storage read errors
  }
  return null;
}

function writePersistedCache(path: string, entry: { at: number; data: unknown }): void {
  try {
    localStorage.setItem(STORAGE_PREFIX + path, JSON.stringify(entry));
  } catch {
    // ignore quota errors
  }
}

export function invalidateClientCache(): void {
  responseCache.clear();
  try {
    const keysToRemove: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(STORAGE_PREFIX)) {
        keysToRemove.push(k);
      }
    }
    for (const k of keysToRemove) {
      localStorage.removeItem(k);
    }
  } catch {
    // ignore
  }
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

async function fetchFreshJson<T>(path: string): Promise<T> {
  const existing = inflightRequests.get(path);
  if (existing) {
    return (await existing) as T;
  }
  const promise = (async () => {
    const response = await fetch(`${API_BASE_URL}${path}`);
    if (!response.ok) {
      const body = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new ApiError(response.status, body.detail ?? response.statusText);
    }
    const parsed = (await response.json()) as T;
    const entry = { at: Date.now(), data: parsed };
    responseCache.set(path, entry);
    writePersistedCache(path, entry);
    return parsed;
  })();

  inflightRequests.set(path, promise);
  try {
    return await promise;
  } finally {
    inflightRequests.delete(path);
  }
}

async function getJson<T>(path: string): Promise<T> {
  const cached = responseCache.get(path) ?? readPersistedCache(path);
  if (cached) {
    if (Date.now() - cached.at > CLIENT_CACHE_TTL_MS) {
      fetchFreshJson<T>(path).catch(() => {});
    }
    return cached.data as T;
  }
  if (path in INSTANT_BOOTSTRAP_SEED) {
    fetchFreshJson<T>(path).catch(() => {});
    return INSTANT_BOOTSTRAP_SEED[path] as T;
  }
  return fetchFreshJson<T>(path);
}

async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, body.detail ?? response.statusText);
  }
  invalidateClientCache();
  return (await response.json()) as T;
}

export function fetchSummary(): Promise<SummaryOut> {
  return getJson<SummaryOut>("/summary");
}

export function fetchPlan(): Promise<PlanOut> {
  return getJson<PlanOut>("/plan");
}

export function fetchRecommendations(): Promise<RecommendationOut[]> {
  return getJson<RecommendationOut[]>("/recommendations");
}

export function fetchTree(
  path: string | null,
  volume?: string | null,
): Promise<TreeOut> {
  const params = new URLSearchParams();
  if (path !== null) params.set("path", path);
  if (volume) params.set("volume", volume);
  const qs = params.toString();
  return getJson<TreeOut>(`/tree${qs ? `?${qs}` : ""}`);
}

export function fetchSearch(
  query: string,
  volume?: string | null,
): Promise<SearchResultsOut> {
  const params = new URLSearchParams();
  if (query.trim()) params.set("q", query.trim());
  if (volume) params.set("volume", volume);
  const qs = params.toString();
  return getJson<SearchResultsOut>(`/search${qs ? `?${qs}` : ""}`);
}

export function scanVolumeNow(
  target: string,
  restart = false,
): Promise<ScanVolumeOut> {
  return postJson<ScanVolumeOut>("/actions/scan", { target, restart });
}

export function fetchCategories(): Promise<CategoryTotalOut[]> {
  return getJson<CategoryTotalOut[]>("/categories");
}

export function fetchCategoriesDetail(
  volume?: string | null,
): Promise<CategoriesDetailOut> {
  const qs = volume ? `?volume=${encodeURIComponent(volume)}` : "";
  return getJson<CategoriesDetailOut>(`/categories/detail${qs}`);
}

export function fetchSnapshots(volume: string | null): Promise<SnapshotOut[]> {
  const query = volume === null ? "" : `?volume=${encodeURIComponent(volume)}`;
  return getJson<SnapshotOut[]>(`/snapshots${query}`);
}

export function fetchGrowth(volume: string | null): Promise<GrowthOut> {
  const query = volume === null ? "" : `?volume=${encodeURIComponent(volume)}`;
  return getJson<GrowthOut>(`/growth${query}`);
}

export function fetchPrograms(): Promise<ProgramsOverviewOut> {
  return getJson<ProgramsOverviewOut>("/programs");
}

export function fetchProgramFootprints(name: string): Promise<FootprintOut[]> {
  return getJson<FootprintOut[]>(
    `/programs/footprints?name=${encodeURIComponent(name)}`,
  );
}

export function fetchLeftovers(name: string): Promise<FootprintOut[]> {
  return getJson<FootprintOut[]>(
    `/leftovers?name=${encodeURIComponent(name)}`,
  );
}

export function fetchLimits(): Promise<LimitsOverviewOut> {
  return getJson<LimitsOverviewOut>("/limits");
}

export function updateVolumeLimit(
  volume: string,
  warnAtPercent: number,
  minFreeGb: number | null,
): Promise<LimitsOverviewOut> {
  return postJson<LimitsOverviewOut>("/limits/volume", {
    volume,
    warn_at_percent: warnAtPercent,
    min_free_gb: minFreeGb,
  });
}

export function updateBudget(
  target: string,
  maxGb: number | null,
): Promise<LimitsOverviewOut> {
  return postJson<LimitsOverviewOut>("/limits/budget", {
    target,
    max_gb: maxGb,
  });
}

export function runCheckAlertsNow(): Promise<{
  alerts_fired: number;
  items: { kind: string; target: string }[];
}> {
  return postJson<{
    alerts_fired: number;
    items: { kind: string; target: string }[];
  }>("/actions/check-alerts", {});
}

export function openInExplorer(path: string): Promise<{ opened: boolean }> {
  return postJson<{ opened: boolean }>("/actions/open-explorer", { path });
}

export function uninstallProgram(
  programName: string,
): Promise<UninstallFlowOut> {
  return postJson<UninstallFlowOut>("/actions/uninstall", {
    program_name: programName,
  });
}

export async function trashPaths(paths: string[]): Promise<TrashBatchOut> {
  const res = await postJson<TrashBatchOut>("/actions/trash", { paths });
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("discoclaro:trash-updated"));
  }
  return res;
}

export function runTerminalCommand(
  command: string,
  cwd?: string,
): Promise<TerminalCommandOut> {
  return postJson<TerminalCommandOut>("/actions/terminal", {
    command,
    cwd: cwd ?? null,
  });
}

export function cleanSafeBatch(): Promise<BatchCleanOut> {
  return postJson<BatchCleanOut>("/actions/clean-safe", {});
}

export function fetchDuplicates(
  volume?: string | null,
): Promise<DuplicatesOverviewOut> {
  const qs = volume ? `?volume=${encodeURIComponent(volume)}` : "";
  return getJson<DuplicatesOverviewOut>(`/duplicates${qs}`);
}

export function fetchScheduleStatus(): Promise<ScheduleOverviewOut> {
  return getJson<ScheduleOverviewOut>("/schedule");
}

export function installScheduleTasks(
  target = "C:",
): Promise<ScheduleOverviewOut> {
  return postJson<ScheduleOverviewOut>("/schedule/install", { target });
}

export function removeScheduleTasks(): Promise<ScheduleOverviewOut> {
  return postJson<ScheduleOverviewOut>("/schedule/remove", {});
}

export function createDesktopShortcut(): Promise<{
  created: boolean;
  path: string;
}> {
  return postJson<{ created: boolean; path: string }>(
    "/actions/desktop-shortcut",
    {},
  );
}

export function fetchDevProjects(
  staleDays = 30,
): Promise<DevProjectsOverviewOut> {
  return getJson<DevProjectsOverviewOut>(
    `/dev-projects?stale_days=${staleDays}`,
  );
}

export function fetchVhdxDisks(): Promise<VhdxDiskOut[]> {
  return getJson<VhdxDiskOut[]>("/vhdx");
}

export function compactVhdxNow(path: string): Promise<VhdxCompactOut> {
  return postJson<VhdxCompactOut>("/actions/compact-vhdx", { path });
}

export function fetchCleanupHistory(): Promise<CleanupHistoryOut> {
  return getJson<CleanupHistoryOut>("/history");
}

export function fetchMigrationAssistant(): Promise<MigrationAssistantOut> {
  return getJson<MigrationAssistantOut>("/migration-assistant");
}

export function fetchRecycleBinStatus(): Promise<RecycleBinStatusOut> {
  return getJson<RecycleBinStatusOut>("/recycle-bin");
}

export function emptyRecycleBinNow(): Promise<RecycleBinEmptyOut> {
  return postJson<RecycleBinEmptyOut>("/actions/empty-recycle-bin", {});
}

export function fetchDiskHealth(): Promise<PhysicalDiskHealthOut[]> {
  return fetchFreshJson<PhysicalDiskHealthOut[]>("/disk-health");
}

export function readSmartElevatedNow(): Promise<PhysicalDiskHealthOut[]> {
  return postJson<PhysicalDiskHealthOut[]>("/actions/read-smart-admin", {});
}

export function launchSystemTrayNow(): Promise<{
  launched: boolean;
  pid: number;
}> {
  return postJson<{ launched: boolean; pid: number }>(
    "/actions/launch-tray",
    {},
  );
}
