export type Mode = "standard" | "spot_color";

export type RGB = [number, number, number];

export interface JobParams {
  mode: Mode;
  max_dim: number;
  base_h: number;
  max_h: number;
  layer_height: number;
  max_res_cap: number;
  white_clip?: number | null;
  black_clip?: number | null;
  color_mode?: number | null;
  /** Two colours only: fraction of a zone's area that must be inked to print as ink. */
  bw_coverage?: number | null;
  sampled_values?: number[] | null;
  color_changes_z?: number[] | null;
  spot_accents: RGB[];
  spot_coverage: number;
  autodetect_accents?: boolean;
}

export interface Analysis {
  width: number;
  height: number;
  halftone_pct: number;
  color_mode: number;
  suggested_white_clip: number;
  suggested_midtones: [number, number];
  suggested_sampled_values: number[];
  suggested_color_changes_z: number[];
  suggested_accents: RGB[];
  bw_ink_level: number;
}

export type JobStatus = "queued" | "running" | "done" | "error" | "expired";

export interface Artifact {
  kind: "stl" | "3mf";
  filename: string;
  bytes: number;
  download_url: string;
}

export interface FilamentChange {
  z: number;
  color: string | null;
}

export interface JobView {
  job_id: string;
  status: JobStatus;
  progress: number;
  message: string;
  mode: string;
  created_at: string;
  expires_at: string | null;
  downloaded_at: string | null;
  duration_s: number | null;
  error: string | null;
  artifacts: Artifact[];
  filament_changes: FilamentChange[];
}

export const DEFAULT_PARAMS: JobParams = {
  mode: "standard",
  max_dim: 180,
  base_h: 1.0,
  max_h: 2.4,
  layer_height: 0.2,
  max_res_cap: 800,
  spot_accents: [],
  spot_coverage: 40,
  autodetect_accents: true,
};


/** Una generazione passata: quello che ne resta quando i file sono scaduti. */
export interface HistoryEntry {
  id: string;
  created_at: string;
  image_name: string | null;
  mode: Mode;
  color_mode: number | null;
  status: "queued" | "running" | "done" | "error" | "expired";
  /** Solo finche' i file ci sono. */
  expires_at: string | null;
  preview_url: string | null;
  /** Falso quando la sorgente e' stata potata: la voce resta, il clic no. */
  can_regenerate: boolean;
  artifacts: Artifact[];
}

export interface HistoryList {
  entries: HistoryEntry[];
  /** Quante generazioni per account conservano la sorgente. */
  keep_sources: number;
}

/**
 * Le regole del servizio, servite dal server perche' non le riscriva il testo.
 * Sono uguali per tutti: quanto ti resta *a te* sta in `Quota`.
 */
export interface Limits {
  anon_generations: number;
  user_generations: number;
  window_h: number;
  retention_h: number;
  post_download_h: number;
  max_upload_mb: number;
  max_dim_mm: number;
  max_res_cap: number;
  modes: string[];
}

export interface Quota {
  plan: "anonymous" | "registered" | "unlimited";
  /** null quando il piano non ha tetto. */
  limit: number | null;
  used: number;
  remaining: number | null;
  reset_at: string | null;
}
