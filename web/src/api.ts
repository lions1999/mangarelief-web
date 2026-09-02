/**
 * The only place that knows the API exists.
 *
 * `mockup` is deliberately isolated behind this function: phase 4 replaces the
 * server round-trip with a client-side port of the classifier, and nothing in
 * the UI should have to change when it does.
 */
import type { Analysis, JobParams, JobView } from "./types";

const BASE = (import.meta.env.VITE_API_URL ?? "http://localhost:8080").replace(/\/$/, "");

class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function failure(res: Response): Promise<never> {
  let detail = res.statusText;
  try {
    const body = await res.json();
    detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    /* non-JSON error body: keep the status text */
  }
  throw new ApiError(detail, res.status);
}

function form(file: File, params?: Partial<JobParams>, turnstileToken?: string): FormData {
  const fd = new FormData();
  fd.append("image", file);
  if (params) fd.append("params", JSON.stringify(params));
  if (turnstileToken) fd.append("turnstile_token", turnstileToken);
  return fd;
}

export async function analyze(file: File, params?: Partial<JobParams>): Promise<Analysis> {
  const res = await fetch(`${BASE}/api/analyze`, { method: "POST", body: form(file, params) });
  return res.ok ? res.json() : failure(res);
}

/** A PNG object URL of how the image will be classified. Caller revokes it. */
export async function mockup(
  file: File,
  params: Partial<JobParams>,
  signal?: AbortSignal,
): Promise<string> {
  const res = await fetch(`${BASE}/api/mockup`, {
    method: "POST",
    body: form(file, params),
    signal,
  });
  if (!res.ok) return failure(res);
  return URL.createObjectURL(await res.blob());
}

export async function createJob(
  file: File,
  params: JobParams,
  turnstileToken?: string,
): Promise<{ job_id: string; notes: string | null }> {
  const res = await fetch(`${BASE}/api/jobs`, {
    method: "POST",
    body: form(file, params, turnstileToken),
  });
  if (!res.ok) return failure(res);
  const notes = res.headers.get("X-MangaRelief-Notes");
  const body = await res.json();
  return { job_id: body.job_id, notes };
}

export async function getJob(jobId: string): Promise<JobView> {
  const res = await fetch(`${BASE}/api/jobs/${jobId}`);
  return res.ok ? res.json() : failure(res);
}

/** Viewer URL: streams the same bytes without starting the 24h countdown. */
export function previewUrl(jobId: string, kind: "stl" | "3mf" = "stl"): string {
  return `${BASE}/api/jobs/${jobId}/artifacts/${kind}?preview=true`;
}

export function downloadUrl(jobId: string, kind: "stl" | "3mf"): string {
  return `${BASE}/api/jobs/${jobId}/artifacts/${kind}`;
}

export { ApiError };
