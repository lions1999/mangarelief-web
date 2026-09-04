/**
 * The only place that knows the API exists.
 *
 * `mockup` is deliberately isolated behind this function: phase 4 replaces the
 * server round-trip with a client-side port of the classifier, and nothing in
 * the UI should have to change when it does.
 */
import { deviceId, expiringSoon, getSession, setSession, type Session } from "./session";
import type { Analysis, JobParams, JobView, Quota } from "./types";

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

/**
 * Le intestazioni comuni a ogni chiamata: chi sei e da quale browser.
 *
 * Rinnova il token prima che scada invece di aspettare un 401: una sessione
 * che muore a metà di un caricamento è un errore che l'utente non capirebbe.
 * Se il rinnovo fallisce si prosegue da anonimi, perché il sito deve
 * continuare a funzionare anche quando l'accesso non funziona.
 */
export async function authHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = { "X-MangaRelief-Device": deviceId() };
  let s = getSession();
  if (s && expiringSoon(s) && s.refresh_token) {
    try {
      s = await refreshSession(s.refresh_token);
      setSession(s);
    } catch {
      setSession(null);
      s = null;
    }
  }
  if (s) headers.Authorization = `Bearer ${s.access_token}`;
  return headers;
}

export async function requestCode(email: string): Promise<void> {
  const res = await fetch(`${BASE}/api/auth/code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) await failure(res);
}

export async function verifyCode(email: string, code: string):
    Promise<Session & { linked: number }> {
  const res = await fetch(`${BASE}/api/auth/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code, device_id: deviceId() }),
  });
  return res.ok ? res.json() : failure(res);
}

export async function refreshSession(refresh_token: string): Promise<Session> {
  const res = await fetch(`${BASE}/api/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  });
  return res.ok ? res.json() : failure(res);
}

export async function getQuota(): Promise<Quota> {
  const res = await fetch(`${BASE}/api/quota`, { headers: await authHeaders() });
  return res.ok ? res.json() : failure(res);
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
): Promise<{ url: string; ambiguous: number | null }> {
  const res = await fetch(`${BASE}/api/mockup`, {
    method: "POST",
    body: form(file, params),
    signal,
  });
  if (!res.ok) return failure(res);
  // Two colours only: the server says how much of the artwork sits near the
  // coverage cut, on the same call that draws it, so the warning tracks the
  // slider instead of describing the upload-time default.
  const ambiguousRaw = res.headers.get("X-MangaRelief-Ambiguous");
  return {
    url: URL.createObjectURL(await res.blob()),
    ambiguous: ambiguousRaw === null ? null : Number(ambiguousRaw),
  };
}

export async function createJob(
  file: File,
  params: JobParams,
  turnstileToken?: string,
): Promise<{ job_id: string; notes: string | null }> {
  const res = await fetch(`${BASE}/api/jobs`, {
    method: "POST",
    headers: await authHeaders(),
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
