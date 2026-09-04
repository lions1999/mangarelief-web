/**
 * App shell: a fixed settings sidebar and one stage that fills the rest.
 *
 * The earlier layout was a centred two-column page, which meant the controls
 * and the preview both got half of 1240px on any screen and the previews ended
 * up postage-stamp sized — and reaching the Generate button meant scrolling.
 * Here the page itself never scrolls: the sidebar scrolls its own contents,
 * Generate is pinned to its foot, and the artwork or the model gets every
 * pixel that is left.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Dropzone from "./components/Dropzone";
import LookPanel from "./components/LookPanel";
import PrintPanel from "./components/PrintPanel";
import SpotPanel from "./components/SpotPanel";
import History from "./components/History";
import Stage, { type StageView } from "./components/Stage";
import TonesPanel from "./components/TonesPanel";
import AccountBar from "./components/AccountBar";
import SignIn from "./components/SignIn";
import Welcome from "./components/Welcome";
import Turnstile, { turnstileEnabled } from "./components/Turnstile";
import { plural, windowLabel } from "./copy";
import { useAccents } from "./hooks/useAccents";
import { useTones } from "./hooks/useTones";
import { ApiError, analyze, createJob, getJob, getLimits, getQuota, regenerate } from "./api";
import { getSession, markWelcomeSeen, setSession, welcomeSeen, type Session } from "./session";
import { DEFAULT_PARAMS, type Analysis, type HistoryEntry, type JobParams, type JobView, type Limits, type Quota, type RGB } from "./types";

const POLL_MS = 1200;

export default function App() {
  const [file, setFile] = useState<File | null>(null);
  const [params, setParams] = useState<JobParams>(DEFAULT_PARAMS);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [job, setJob] = useState<JobView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notes, setNotes] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [view, setView] = useState<StageView>("art");
  const [ambiguous, setAmbiguous] = useState<number | null>(null);
  const [session, setSessionState] = useState<Session | null>(() => getSession());
  const [quota, setQuota] = useState<Quota | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  const [limits, setLimits] = useState<Limits | null>(null);
  // Deciso una volta all'avvio: legato allo stato vivo, il benvenuto
  // riapparirebbe da solo appena qualcuno esce dall'account.
  const [welcome, setWelcome] = useState(() => !welcomeSeen() && !getSession());
  // Cambia a ogni generazione riuscita: e' il segnale che la cronologia ha una
  // voce in piu' da mostrare la prossima volta che si apre.
  const [historyKey, setHistoryKey] = useState(0);
  const [linkedNote, setLinkedNote] = useState("");

  const poll = useRef<number | undefined>(undefined);
  const stage = useRef<HTMLDivElement>(null);

  const refreshQuota = useCallback(() => {
    getQuota().then(setQuota).catch((err) => {
      setQuota(null);
      // Sessione rifiutata dal server: revocata, o l'account e' stato
      // cancellato. Restare "collegati" con un contatore che non arriva mai
      // e' lo stato peggiore — sembra tutto a posto e non funziona niente.
      if (err instanceof ApiError && err.status === 401) {
        setSession(null);
        setSessionState(null);
      }
    });
  }, []);

  useEffect(() => { refreshQuota(); }, [refreshQuota, session]);

  useEffect(() => { getLimits().then(setLimits).catch(() => setLimits(null)); }, []);

  useEffect(() => {
    // Chi e' gia' dentro il benvenuto lo ha gia' avuto, o e' arrivato prima
    // che esistesse: in nessuno dei due casi va mostrato adesso.
    if (!welcome) markWelcomeSeen();
  }, [welcome]);

  const closeWelcome = useCallback((thenSignIn: boolean) => {
    markWelcomeSeen();
    setWelcome(false);
    if (thenSignIn) setSigningIn(true);
  }, []);

  const patch = useCallback(
    (p: Partial<JobParams>) => setParams((old) => ({ ...old, ...p })),
    [],
  );

  const maxMb = limits?.max_upload_mb ?? null;
  const tones = useTones(params, analysis, patch);
  const accents = useAccents(params, patch);
  const onPick = (colour: RGB) =>
    params.mode === "spot_color" ? accents.pick(colour) : tones.pick(colour);

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
    setView("art");
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
      refreshQuota();
      if (job.status === "done") setHistoryKey((n) => n + 1);
    }
  }, [job, refreshQuota]);

  const generate = async () => {
    if (!file) return;
    reset();
    setBusy(true);
    setView("model");
    // Stacked on a phone the stage sits below the whole sidebar, so a press on
    // Generate would otherwise show nothing happening.
    stage.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      const { job_id, notes } = await createJob(file, params, token || undefined);
      setNotes(notes);
      setJob(await getJob(job_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not start the generation");
      setBusy(false);
      setView("art");
    }
  };

  /** Rivedere un modello di ieri: nessuna immagine da ricaricare, si apre. */
  const openPast = async (entry: HistoryEntry) => {
    reset();
    try {
      setJob(await getJob(entry.id));
      setView("model");
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not open that one");
    }
  };

  /** Rifarla costa una generazione della giornata, come farla la prima volta. */
  const redo = async (entry: HistoryEntry) => {
    reset();
    setBusy(true);
    setView("model");
    try {
      const { job_id } = await regenerate(entry.id);
      setJob(await getJob(job_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not redo that one");
      setBusy(false);
      setView("history");
    }
  };

  return (
    <div className="shell">
      {welcome && limits && (
        <Welcome
          limits={limits}
          onStart={() => closeWelcome(false)}
          onSignIn={() => closeWelcome(true)}
        />
      )}
      {signingIn && (
        <SignIn
          onCancel={() => setSigningIn(false)}
          onDone={(linked) => {
            setSigningIn(false);
            setSessionState(getSession());
            // Detto subito: scoprire dopo che le prove erano state scalate
            // sembrerebbe un raggiro, anche se era scritto nel benvenuto.
            if (linked > 0) {
              setLinkedNote(`The ${linked} generation${linked === 1 ? "" : "s"} `
                + "already made from this browser now count on your account.");
            }
          }}
        />
      )}
      <aside className="side">
        <div className="side-head">
          <h1>MangaRelief</h1>
          <p>A panel in, a printable multi-colour relief out.</p>
          <AccountBar
            session={session}
            quota={quota}
            onSignIn={() => setSigningIn(true)}
            onSignOut={() => {
              setSession(null);
              setSessionState(null);
              if (view === "history") setView(file ? "art" : "art");
            }}
            onHistory={() => setView("history")}
          />
        </div>

        <div className="side-body">
          <section className="panel">
            <h2>Artwork</h2>
            {file ? (
              <>
                <p className="hint file-line">
                  <strong>{file.name}</strong>
                  {analysis && ` · ${analysis.width} × ${analysis.height} px`}
                </p>
                <Dropzone onFile={onFile} disabled={busy} compact />
              </>
            ) : (
              <Dropzone onFile={onFile} disabled={busy} maxMb={maxMb} compact />
            )}
          </section>

          <LookPanel
            params={params}
            analysis={analysis}
            disabled={busy}
            ambiguous={ambiguous}
            onChange={patch}
          />

          {/* The colour controls come before the physical ones: they are what
              the preview on the stage answers to, and they are why anyone is
              looking at the artwork in the first place. */}
          {file && params.mode === "standard" && (
            <TonesPanel
              tones={tones}
              disabled={busy}
              coverage={params.bw_coverage ?? 0.35}
              inkLevel={analysis?.bw_ink_level ?? null}
              onCoverage={(v) => patch({ bw_coverage: v })}
            />
          )}

          {file && params.mode === "spot_color" && (
            <SpotPanel
              accents={accents}
              params={params}
              suggested={analysis?.suggested_accents ?? []}
              disabled={busy}
              onChange={patch}
            />
          )}

          <PrintPanel params={params} disabled={busy}
                      maxRes={limits?.max_res_cap ?? null} onChange={patch} />
        </div>

        <div className="side-foot">
          {turnstileEnabled && file && <Turnstile onToken={setToken} />}
          <button className="primary" onClick={generate} disabled={busy || !file}>
            {busy ? "Generating…" : "Generate model"}
          </button>
          {error && <p className="field-error">{error}</p>}
          {notes && <p className="hint">{notes}</p>}
          {linkedNote && <p className="hint">{linkedNote}</p>}
          <p className="hint small">
            {/* Il numero viene dalla quota vera: scritto a mano invecchiava a
                ogni cambio di piano, e prima diceva "5 per hour" quando erano
                gia' diventate 2 al giorno. */}
            Draft resolution{quota && quota.limit != null && limits
              && ` · ${plural(quota.limit, "generation")} ${windowLabel(limits.window_h)}`}
          </p>
        </div>
      </aside>

      <main ref={stage} className="main">
        {view === "history" ? (
          <History
            reloadKey={historyKey}
            busy={busy}
            onOpen={openPast}
            onRegenerate={redo}
            onClose={() => setView(job ? "model" : "art")}
          />
        ) : (
          <Stage
            file={file}
            params={params}
            analysis={analysis}
            job={job}
            view={view}
            disabled={busy}
            onView={setView}
            onFile={onFile}
            limits={limits}
            onPick={onPick}
            onAmbiguity={setAmbiguous}
          />
        )}
      </main>
    </div>
  );
}
