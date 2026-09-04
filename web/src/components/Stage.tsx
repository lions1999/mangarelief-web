/**
 * The stage: the one big area that always shows the thing being made.
 *
 * Two views share it — the artwork you sample colours from, and the finished
 * model. They are tabs rather than one replacing the other, because the loop
 * this app is used in is "look at the model, go back, move a tone, generate
 * again"; destroying the picker on success would make that a page reload.
 */
import { Suspense, lazy, useEffect } from "react";
import Dropzone from "./Dropzone";
import PickableImage from "./PickableImage";
import ProgressBar from "./ProgressBar";
import { useMockup } from "../hooks/useMockup";
import { downloadUrl, previewUrl } from "../api";
import type { Analysis, JobParams, JobView, Limits, RGB } from "../types";

// three.js is ~600 kB of the bundle and is useless until a model exists, so it
// is fetched the first time a generation finishes, not on page load.
const ModelViewer = lazy(() => import("./ModelViewer"));

/** "history" la disegna App, non lo stage: e' l'unica vista che non parla di
 *  un'immagine caricata. Sta nell'unione perche' e' lo stesso selettore. */
export type StageView = "art" | "model" | "history";

interface Props {
  file: File | null;
  params: JobParams;
  analysis: Analysis | null;
  job: JobView | null;
  view: StageView;
  disabled: boolean;
  onView: (view: StageView) => void;
  onFile: (file: File) => void;
  /** Le regole del servizio: i numeri che il testo qui dentro cita. */
  limits: Limits | null;
  onPick: (colour: RGB) => void;
  /** Live: share of the artwork near the two-colour cut, from the preview call. */
  onAmbiguity: (value: number | null) => void;
}

function expiryLabel(iso: string | null): string {
  if (!iso) return "";
  const hours = (new Date(iso).getTime() - Date.now()) / 3_600_000;
  if (hours <= 0) return "expired";
  if (hours < 1) return `${Math.round(hours * 60)} minutes`;
  return `${Math.round(hours)} hours`;
}

export default function Stage({
  file, params, analysis, job, view, disabled, onView, onFile, limits, onPick, onAmbiguity,
}: Props) {
  const maxMb = limits?.max_upload_mb ?? null;
  const spot = params.mode === "spot_color";
  const twoColours = (params.color_mode ?? analysis?.color_mode) === 2;
  const tones = params.sampled_values ?? analysis?.suggested_sampled_values;

  const { url: mockup, busy: mockupBusy, error: mockupError, ambiguous } = useMockup(
    file,
    spot
      ? {
          mode: "spot_color",
          spot_accents: params.spot_accents,
          spot_coverage: params.spot_coverage,
          autodetect_accents: params.spot_accents.length === 0,
        }
      : {
          mode: "standard",
          // The colour count has to reach the preview, otherwise switching from
          // 4 to 2 changes the mesh but not the picture — which defeats the
          // point of having the two controls in sight of each other.
          color_mode: params.color_mode ?? analysis?.color_mode,
          sampled_values: tones ?? undefined,
          color_changes_z: params.color_changes_z ?? undefined,
          bw_coverage: params.bw_coverage ?? undefined,
        },
    [
      params.mode,
      params.spot_accents.map((a) => a.join()).join("|"),
      params.spot_coverage,
      tones?.join(),
      params.color_mode,
      params.color_changes_z?.join(),
      params.bw_coverage,
    ],
  );
  useEffect(() => { onAmbiguity(ambiguous); }, [ambiguous, onAmbiguity]);

  const running = job?.status === "queued" || job?.status === "running";
  const done = job?.status === "done";

  // Senza immagine c'e' ancora qualcosa da mostrare, se si e' aperto un
  // modello dalla cronologia: e' il gesto «rivedo quello di ieri», e non
  // richiede di ricaricare la tavola.
  if (!file && !job) {
    return (
      <div className="stage">
        <div className="stage-empty">
          <Dropzone onFile={onFile} maxMb={maxMb} />
          <p className="hint">
            A manga panel, a logo, a line drawing — anything with clear tonal
            areas. Nothing is generated until you ask for it, and results are
            deleted{limits ? ` within ${limits.retention_h} hours` : " on a schedule"}.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="stage">
      <div className="stage-head">
        <div className="tabs">
          <button
            type="button"
            className={`tab${view === "art" ? " active" : ""}`}
            disabled={!file}
            onClick={() => onView("art")}
          >
            Artwork
          </button>
          <button
            type="button"
            className={`tab${view === "model" ? " active" : ""}`}
            disabled={!job && !running}
            onClick={() => onView("model")}
          >
            Model
          </button>
        </div>
        <span className="stage-note">
          {view === "art"
            ? spot
              ? "Click the artwork to pick the selected accent."
              : twoColours
                ? "Two colours: set the ink coverage in the sidebar. The preview follows."
                : "Click the artwork to sample the selected tone."
            : done
              ? `Generated in ${job?.duration_s?.toFixed(1)}s`
              : ""}
        </span>
      </div>

      {/* `file` puo' mancare: si arriva qui anche aprendo un modello dalla
          cronologia, e in quel caso la scheda Artwork e' spenta. */}
      {view === "art" && file && (
        <div className="stage-art">
          <figure>
            <div className="frame">
              <PickableImage file={file} disabled={disabled || (!spot && twoColours)} onPick={onPick} />
            </div>
            <figcaption>Source — click to sample · scroll to zoom, drag to pan</figcaption>
          </figure>
          <figure>
            <div className="frame">
              {mockup
                ? <img src={mockup} alt="Print preview" />
                : <span className="hint">Preparing the preview…</span>}
            </div>
            <figcaption>
              {mockupBusy
                ? "Updating preview…"
                : spot ? "How it will print" : "One shade per filament"}
            </figcaption>
          </figure>
          {mockupError && <p className="field-error">{mockupError}</p>}
        </div>
      )}

      {view === "model" && (
        <div className="stage-model">
          <div className="stage-viewer">
            {running && (
              <div className="stage-waiting">
                <ProgressBar
                  progress={job?.progress ?? 0}
                  message={job?.message ?? ""}
                  cold={(job?.progress ?? 0) === 0}
                />
              </div>
            )}
            {done && job && (
              <Suspense fallback={<div className="viewer-canvas" />}>
                <ModelViewer url={previewUrl(job.job_id)} />
              </Suspense>
            )}
            {job?.status === "expired" && (
              <div className="stage-waiting">
                <p className="field-error">This result has expired. Generate it again.</p>
              </div>
            )}
            {job?.status === "error" && (
              <div className="stage-waiting">
                <p className="field-error">{job.error ?? "generation failed"}</p>
              </div>
            )}
          </div>

          {done && job && (
            <aside className="stage-rail">
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
                        {c.color && <span className="chip" style={{ background: c.color }} />}
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
                Files are deleted in {expiryLabel(job.expires_at)}
                {limits && `, and ${limits.post_download_h} hours after your
                first download`} — save them somewhere.
              </p>
            </aside>
          )}
        </div>
      )}
    </div>
  );
}
