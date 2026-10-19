// Espejo de los modelos de salida de `interfaces/api.py` (T3.1/T3.3).
// Mismos nombres de campo que la API, para no tener que traducir en cada lugar.

export interface VolumeOut {
  name: string;
  root: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
}

export interface UnexplainedOut {
  size_bytes: number;
  percent_of_used: number;
  exceeds_threshold: boolean;
}

export interface FolderGrowthOut {
  path: string;
  depth: number;
  before_bytes: number;
  after_bytes: number;
  grown_bytes: number;
  is_new: boolean;
}

export interface VolumeSummaryOut {
  volume: VolumeOut;
  snapshot_id: number | null;
  taken_at: string | null;
  unexplained: UnexplainedOut | null;
  days_until_full: number | null;
  top_growth: FolderGrowthOut | null;
}

export interface SummaryOut {
  volumes: VolumeSummaryOut[];
}

export type PlanLevel = "safe" | "review" | "move";

export interface PlanItemOut {
  level: PlanLevel;
  program: string;
  name: string;
  size_bytes: number;
  reason: string;
  target: string;
}

export interface PlanOut {
  safe_bytes: number;
  review_bytes: number;
  move_bytes: number;
  items: PlanItemOut[];
}
