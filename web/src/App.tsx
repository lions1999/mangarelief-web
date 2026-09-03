import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import Dropzone from "./components/Dropzone";

// three.js is ~600 kB of the bundle and is useless until a model exists, so it
// is fetched the first time a generation finishes, not on page load.
const ModelViewer = lazy(() => import("./components/ModelViewer"));
import ParamsPanel from "./components/ParamsPanel";
import ProgressBar from "./components/ProgressBar";
import SpotPanel from "./components/SpotPanel";
import TonesPanel from "./components/TonesPanel";
import Turnstile, { turnstileEnabled } from "./components/Turnstile";
import { analyze, createJob, downloadUrl, getJob, previewUrl } from "./api";
import { DEFAULT_PARAMS, type Analysis, type JobParams, type JobView } from "./types";

const POLL_MS = 1200;

function expiryLabel(iso: string | null): string {
  if (!iso) return "";
  const hours = (new Date(iso).getTime() - Date.now()) / 3_600_000;
  if (hours <= 0) return "expired";
  if (hours < 1) return `${Math.round(hours * 60)} minutes`;
  return `${Math.round(hours)} hours`;
}

export default function App() {
  const [file, setFile] = useState<File | null>(null);
  const [params, setParams] = useState<JobParams>(DEFAULT_PARAMS);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [job, setJob] = useState<JobView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notes, setNotes] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const poll = useRef<number | undefined>(undefined);

  const patch = useCallback(
    (p: Partial<JobParams>) => setParams((old) => ({ ...old, ...p })),
    [],
  );

  const reset = () => {
    setJob(null);
    setError("");
    setNotes(null);
  };

  const onFile = async (f: File) => {
    reset();
    setFile(f);
    setParams(DEFAULT_PARAMS);
    setAnalysis(null);
    try {
      setAnalysis(await analyze(f));
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not read that image");
    }
  };

  // Poll while a job is in flight. Cleared on unmount so a re-render never
  // leaves two timers racing.
  useEffect(() => {
    if (!job || (job.status !== "queued" && job.status !== "running")) return;
    poll.current = window.setTimeout(async () => {
      try {
        setJob(await getJob(job.job_id));
      } catch (err) {
        setError(err instanceof Error ? err.message : "lost contact with the server");
        setBusy(false);
      }
    }, POLL_MS);
    return () => window.clearTimeout(poll.current);
  }, [job]);

  useEffect(() => {
    if (job && (job.status === "done" || job.status === "error" || job.status === "expired")) {
      setBusy(false);
      if (job.status === "error") setError(job.error ?? "generation failed");
    }
  }, [job]);

  const generate = async () => {
    if (!file) return;
    reset();
    setBusy(true);
    try {
      const { job_id, notes } = await createJob(file, params, token || undefined);
      setNotes(notes);
      setJob(await getJob(job_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not start the generation");
      setBusy(false);
    }
  };

  const done = job?.status === "done";
  const running = job?.status === "queued" || job?.status === "running";

  return (
    <div className="app">
      <header>
        <h1>MangaRelief</h1>
        <p>
          Turn a panel into a printable multi-colour relief. One image in, an STL
          and a Bambu-ready 3MF out.
        </p>
      </header>

      <main>
        <div className="column">
          <section className="panel">
            <h2>1 · Your artwork</h2>
            <Dropzone onFile={onFile} disabled={busy} />
            {file && (
              <p className="hint">
                <strong>{file.name}</strong>
                {analysis && ` · ${analysis.width} × ${analysis.height} px`}
              </p>
            )}
          </section>

          {file && (
            <ParamsPanel
              params={params}
              analysis={analysis}
              disabled={busy}
              onChange={patch}
            />
          )}

          {file && params.mode === "standard" && (
            <TonesPanel
              file={file}
              params={params}
              analysis={analysis}
              disabled={busy}
              onChange={patch}
            />
          )}

          {file && params.mode === "spot_color" && (
            <SpotPanel
              file={file}
              params={params}
              suggested={analysis?.suggested_accents ?? []}
              disabled={busy}
              onChange={patch}
            />
          )}

          {file && (
            <section className="panel">
              {turnstileEnabled && <Turnstile onToken={setToken} />}
              <button className="primary" onClick={generate} disabled={busy}>
                {busy ? "Generating…" : "Generate model"}
              </button>
              {error && <p className="field-error">{error}</p>}
              {notes && <p className="hint">{notes}</p>}
            </section>
          )}
        </div>

        <div className="column">
          <section className="panel result">
            <h2>Result</h2>

            {!job && !busy && (
              <p className="hint">
                The 3D preview appears here when the model is ready. Looking at it
                is free and does not start the expiry clock — only downloading does.
              </p>
            )}

            {running && (
              <ProgressBar
                progress={job?.progress ?? 0}
                message={job?.message ?? ""}
                cold={(job?.progress ?? 0) === 0}
              />
            )}

            {done && job && (
              <>
                <Suspense fallback={<div className="viewer-canvas" />}>
                  <ModelViewer url={previewUrl(job.job_id)} />
                </Suspense>
                <div className="downloads">
                  {job.artifacts.map((a) => (
                    <a key={a.kind} className="primary" href={downloadUrl(job.job_id, a.kind)}>
                      Download {a.kind.toUpperCase()}
                      <em>{(a.bytes / 1024 / 1024).toFixed(1)} MB</em>
                    </a>
                  ))}
                </div>
                {job.filament_changes.length > 0 && (
                  <div className="plan">
                    <h3>Filament changes</h3>
                    <ol>
                      {job.filament_changes.map((c, i) => (
                        <li key={c.z}>
                          <span className="plan-z">{c.z.toFixed(2)} mm</span>
                          {c.color && (
                            <span className="chip" style={{ background: c.color }} />
                          )}
                          <span className="plan-what">
                            {c.color ? `load ${c.color}` : `colour ${i + 2}`}
                          </span>
                        </li>
                      ))}
                    </ol>
                    <p className="hint">
                      The 3MF has these placed already — open it in Bambu Studio and
                      print. The plain STL does not carry them, so pause at each
                      height yourself.
                    </p>
                  </div>
                )}

                <p className="hint">
                  Generated in {job.duration_s?.toFixed(1)}s. Files are deleted in{" "}
                  {expiryLabel(job.expires_at)}, and 24 hours after your first
                  download — save them somewhere.
                </p>
              </>
            )}

            {job?.status === "expired" && (
              <p className="field-error">This result has expired. Generate it again.</p>
            )}
          </section>
        </div>
      </main>

      <footer>
        <span>Free generations run at draft resolution · 5 per hour</span>
      </footer>
    </div>
  );
}
