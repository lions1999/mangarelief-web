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
