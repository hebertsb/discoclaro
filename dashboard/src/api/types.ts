// Espejo de los modelos de salida de `interfaces/api.py`.

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

export interface NodeOut {
  path: string;
  size_bytes: number;
  n_files: number;
  depth: number;
}

export interface TreeOut {
  snapshot_id: number;
  volume: string;
  node: NodeOut | null;
  children: NodeOut[];
}

export interface CategoryTotalOut {
  category: string;
  size_bytes: number;
  folders: number;
}

export interface CategorizedFolderOut {
  path: string;
  size_bytes: number;
  n_files: number;
  category: string;
  rule_id: string;
  rule_name: string;
  risk: "safe" | "review" | "do_not_touch";
  explanation: string;
  suggested_command: string | null;
}

export interface CategoriesDetailOut {
  volume: string;
  snapshot_id: number;
  totals: CategoryTotalOut[];
  items: CategorizedFolderOut[];
}

export interface SnapshotOut {
  id: number;
  volume: string;
  taken_at: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  scanned_bytes: number;
  duplicate_bytes: number;
  system_files_bytes: number;
  unreadable_count: number;
  is_admin: boolean;
}

export interface GrowthOut {
  before_snapshot_id: number;
  after_snapshot_id: number;
  before_taken_at: string;
  after_taken_at: string;
  folders: FolderGrowthOut[];
  by_category: {
    category: string;
    before_bytes: number;
    after_bytes: number;
    grown_bytes: number;
  }[] | null;
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

export interface RecommendationOut {
  target: string;
  level: PlanLevel;
  risk: "safe" | "review" | "do_not_touch";
  program: string;
  name: string;
  size_bytes: number;
  rule_id: string | null;
  explanation: string;
  suggested_command: string | null;
  suggested_note: string | null;
  location_path: string | null;
}

export interface FootprintOut {
  path: string;
  size_bytes: number;
  confidence: "high" | "medium" | "low";
  reason: string;
}

export interface ProgramItemOut {
  name: string;
  version: string | null;
  publisher: string | null;
  install_location: string | null;
  install_date: string | null;
  estimated_size_bytes: number | null;
  uninstall_string: string | null;
  total_bytes: number;
  footprints: FootprintOut[];
}

export interface UninstalledProgramOut {
  name: string;
  version: string | null;
  publisher: string | null;
  total_leftover_bytes: number;
  footprints: FootprintOut[];
}

export interface ProgramsOverviewOut {
  installed: ProgramItemOut[];
  uninstalled_with_leftovers: UninstalledProgramOut[];
}

export interface VolumeLimitStatusOut {
  volume: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  used_percent: number;
  warn_at_percent: number;
  min_free_bytes: number | null;
  days_until_full: number | null;
  status: "safe" | "review" | "exceeded";
}

export interface BudgetStatusOut {
  target: string;
  kind: "category" | "folder" | "unmeasured" | "suggested";
  used_bytes: number;
  max_bytes: number;
  used_percent: number;
  status: "safe" | "review" | "exceeded";
}

export interface LimitsOverviewOut {
  volumes: VolumeLimitStatusOut[];
  budgets: BudgetStatusOut[];
}

export interface UninstallFlowOut {
  program_name: string;
  command_executed: string;
  uninstaller_exit_code: number;
  still_in_registry: boolean;
  remaining_footprints: FootprintOut[];
  total_remaining_bytes: number;
}

export interface TrashItemOut {
  path: string;
  freed_bytes: number;
  removed: boolean;
  reason: string;
}

export interface TrashBatchOut {
  items: TrashItemOut[];
  total_freed_bytes: number;
}

export interface TerminalCommandOut {
  command: string;
  exit_code: number;
  stdout: string;
  stderr: string;
  cwd?: string;
}

export interface LargeFileOut {
  path: string;
  size_bytes: number;
  is_new: boolean;
}

export interface SearchResultsOut {
  volume: string;
  snapshot_id: number;
  folders: NodeOut[];
  large_files: LargeFileOut[];
  new_large_files: LargeFileOut[];
}

export interface ScanVolumeOut {
  volume: string;
  snapshot_id: number;
  scanned_bytes: number;
  folders: number;
  files: number;
  duration_s: number;
}

export interface DuplicateGroupOut {
  filename: string;
  size_bytes: number;
  reclaimable_bytes: number;
  fingerprint: string;
  paths: string[];
}

export interface DuplicatesOverviewOut {
  volume: string;
  total_reclaimable_bytes: number;
  groups: DuplicateGroupOut[];
}

export interface BatchCleanItemOut {
  name: string;
  command_or_path: string;
  estimated_bytes: number;
  succeeded: boolean;
  detail: string;
}

export interface BatchCleanOut {
  items: {
    name: string;
    command_or_path: string;
    estimated_bytes: number;
    succeeded: boolean;
    detail: string;
  }[];
  total_estimated_bytes: number;
  succeeded_count: number;
}

export interface ScheduleTaskOut {
  name: string;
  label: string;
  schedule: string;
  installed: boolean;
  altered: boolean;
}

export interface ScheduleOverviewOut {
  tasks: ScheduleTaskOut[];
  all_installed: boolean;
  windowless_mode: boolean;
  target_volume: string;
}

export interface ProjectArtifactOut {
  name: string;
  path: string;
  size_bytes: number;
}

export interface DevProjectOut {
  name: string;
  root_path: string;
  ecosystem: string;
  last_modified_iso: string;
  days_inactive: number;
  is_stale: boolean;
  reclaimable_bytes: number;
  artifacts: ProjectArtifactOut[];
}

export interface DevProjectsOverviewOut {
  total_reclaimable_bytes: number;
  stale_reclaimable_bytes: number;
  projects: DevProjectOut[];
}

export interface VhdxDiskOut {
  name: string;
  path: string;
  size_bytes: number;
  engine: string;
}

export interface VhdxCompactOut {
  path: string;
  before_bytes: number;
  after_bytes: number;
  freed_bytes: number;
  succeeded: boolean;
  detail: string;
}

export interface CleanupHistoryEntryOut {
  timestamp: string;
  action_type: string;
  target: string;
  freed_bytes: number;
  detail: string;
}

export interface CleanupHistoryOut {
  total_freed_bytes: number;
  total_actions: number;
  entries: CleanupHistoryEntryOut[];
}

export interface MigrationCandidateOut {
  id: string;
  title: string;
  category: string;
  source_path: string;
  suggested_destination: string;
  size_bytes: number;
  how_to_move: string;
}

export interface MigrationAssistantOut {
  source_volume: string;
  source_total_bytes: number;
  source_used_bytes: number;
  source_free_bytes: number;
  source_used_percent: number;
  target_volume: string | null;
  target_total_bytes: number;
  target_used_bytes: number;
  target_free_bytes: number;
  target_used_percent: number;
  total_movable_bytes: number;
  projected_source_used_percent: number;
  candidates: MigrationCandidateOut[];
}

export interface RecycleBinStatusOut {
  size_bytes: number;
  item_count: number;
}

export interface RecycleBinEmptyOut {
  before_bytes: number;
  before_items: number;
  after_bytes: number;
  freed_bytes: number;
  succeeded: boolean;
}

export interface PhysicalDiskHealthOut {
  device_id: string;
  friendly_name: string;
  serial_number: string | null;
  firmware_version: string | null;
  media_type: string;
  bus_type: string;
  partition_style: string | null;
  drive_letters: string[];
  health_status: string;
  operational_status: string;
  size_bytes: number;
  logical_sector_size: number | null;
  physical_sector_size: number | null;
  temperature_c: number | null;
  temperature_max_c: number | null;
  wear_percent: number | null;
  life_remaining_percent: number | null;
  power_on_hours: number | null;
  read_errors: number | null;
  write_errors: number | null;
  available_spare_percent?: number | null;
  written_tb?: number | null;
  smart_available: boolean;
}
