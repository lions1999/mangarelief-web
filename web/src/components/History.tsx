/**
 * Le generazioni gia' fatte, come griglia di miniature.
 *
 * Griglia e non elenco per una ragione sola, ma decisiva: il caso in cui una
 * cronologia serve davvero e' «ho provato la stessa tavola a 2, 3 e 4 colori,
 * quale era quella buona?». Un elenco di nomi non risponde — sarebbero tre
 * righe uguali — e nemmeno una griglia di immagini caricate, che sarebbero tre
 * miniature identiche. La miniatura e' il mockup, cioe' *proprio* la differenza
 * fra le tre.
 *
 * Una voce vive piu' a lungo dei suoi file: quelli scadono perche' pesano 9 MB,
 * lei resta perche' ne pesa 0,007. Quindi ogni tessera ha due stati e nessuno
 * dei due e' un errore — scaricabile, oppure scaduta e da rifare.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { forget, getHistory } from "../api";
import Info from "./Info";
import type { HistoryEntry, HistoryList } from "../types";

interface Props {
  /** Cambia quando serve rileggere: dopo una generazione nuova. */
  reloadKey: number;
  busy: boolean;
  onOpen: (entry: HistoryEntry) => void;
  onRegenerate: (entry: HistoryEntry) => void;
  onClose: () => void;
}

const quando = (iso: string): string => {
  const giorni = (Date.now() - new Date(iso).getTime()) / 86_400_000;
  if (giorni < 1 / 24) return "just now";
  if (giorni < 1) return `${Math.round(giorni * 24)} h ago`;
  if (giorni < 2) return "yesterday";
  if (giorni < 30) return `${Math.round(giorni)} days ago`;
  return new Date(iso).toLocaleDateString();
};

const descrizione = (e: HistoryEntry): string =>
  e.mode === "spot_color"
    ? "Spot colour"
    : `Standard${e.color_mode ? ` · ${e.color_mode} colours` : ""}`;

export default function History({ reloadKey, busy, onOpen, onRegenerate, onClose }: Props) {
  const [list, setList] = useState<HistoryList | null>(null);
  const [error, setError] = useState("");
  const [removing, setRemoving] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    getHistory()
      .then(setList)
      .catch((err) => setError(err instanceof Error ? err.message : "could not load your generations"));
  }, []);

  useEffect(() => { load(); }, [load, reloadKey]);

  // Su un telefono le due colonne sono impilate e questa nasce sotto tutta la
  // barra laterale: senza portarcisi, premere «your generations» non sembra
  // fare niente. Dopo il caricamento e non al clic, altrimenti si scorre
  // verso una griglia che non ha ancora altezza e non si arriva in fondo.
  useEffect(() => {
    if (!list || !window.matchMedia("(max-width: 900px)").matches) return;
    root.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [list]);

  const remove = async (e: HistoryEntry) => {
    setRemoving(e.id);
    try {
      await forget(e.id);
      setList((old) =>
        old ? { ...old, entries: old.entries.filter((x) => x.id !== e.id) } : old);
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not delete that one");
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="stage" ref={root}>
      <div className="stage-head">
        <div className="tabs">
          <span className="tab active as-title">
            Your generations
            <Info label="your generations">
              <p>
                The files themselves are deleted on schedule — they are large,
                and the space they take is what limits how many models the
                service can make for everybody.
              </p>
              <p>
                What stays is this: the preview and the settings. The
                {list ? ` last ${list.keep_sources}` : " most recent"} can be
                remade from here with one click, which costs one of the day's
                generations. Older ones keep their place in the list, but need
                the artwork loaded again.
              </p>
            </Info>
          </span>
        </div>
        <button type="button" className="link" onClick={onClose}>close</button>
      </div>

      {error && <p className="field-error stage-pad">{error}</p>}

      {list && list.entries.length === 0 && (
        <div className="stage-empty">
          <p className="hint">
            Nothing here yet. Everything you generate while signed in shows up
            in this list, and stays after the files themselves expire.
          </p>
        </div>
      )}

      {list && list.entries.length > 0 && (
        <div className="grid">
          {list.entries.map((e) => {
            const vivo = e.artifacts.length > 0;
            return (
              <article key={e.id} className={`card${vivo ? "" : " gone"}`}>
                <div className="card-art">
                  {e.preview_url
                    ? <img src={e.preview_url} alt="" loading="lazy" />
                    : <span className="card-noart">no preview</span>}
                </div>
                <div className="card-body">
                  <strong title={e.image_name ?? undefined}>
                    {e.image_name ?? "untitled"}
                  </strong>
                  <span className="card-meta">
                    {descrizione(e)} · {quando(e.created_at)}
                  </span>

                  {vivo ? (
                    <div className="card-actions">
                      <button type="button" className="mini" onClick={() => onOpen(e)}>
                        open
                      </button>
                      {e.artifacts.map((a) => (
                        <a key={a.kind} className="mini" href={a.download_url}>
                          {a.kind.toUpperCase()}
                        </a>
                      ))}
                    </div>
                  ) : (
                    <div className="card-actions">
                      {/* Non "errore": scadere e' quello che i file fanno di
                          mestiere, ed e' scritto fin dal benvenuto. */}
                      <span className="card-state">files expired</span>
                      {e.can_regenerate ? (
                        <button type="button" className="mini accent" disabled={busy}
                                onClick={() => onRegenerate(e)}>
                          regenerate
                        </button>
                      ) : (
                        <span className="card-state dim">load the artwork again</span>
                      )}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  className="card-forget"
                  aria-label={`Delete ${e.image_name ?? "this generation"} from your history`}
                  disabled={removing === e.id}
                  onClick={() => remove(e)}
                >
                  ×
                </button>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
