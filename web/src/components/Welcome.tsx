/**
 * Il primo schermo: cosa fa questo sito, cosa costa, e cosa resta di te.
 *
 * Compare una volta sola, prima di qualunque cosa. Le tre regole che racconta
 * — quante generazioni, che le prove si scalano quando ti registri, che i file
 * scadono — sono tutte cose che, scoperte dopo, sembrano un raggiro: la
 * generazione rifiutata a meta' lavoro, il conteggio che non riparte da zero,
 * il modello sparito dal link salvato ieri. Dirle prima costa dieci righe.
 *
 * Nessun numero e' scritto qui dentro: arrivano da `/api/limits`, cioe' dalla
 * configurazione del server. E' la stessa ragione per cui il piede della barra
 * laterale ha smesso di annunciare "5 per hour" quando il limite era altro.
 * Se quella chiamata non risponde il benvenuto non si apre — meglio non dirlo
 * che dirlo sbagliato, e chi non raggiunge il server non puo' generare comunque.
 */
import { useEffect } from "react";

import { plural, windowLabel } from "../copy";
import type { Limits } from "../types";

interface Props {
  limits: Limits;
  onStart: () => void;
  onSignIn: () => void;
}

export default function Welcome({ limits, onStart, onSignIn }: Props) {
  // Esc chiude come Start: una finestra che si apre da sola e non si chiude
  // da tastiera e' una trappola per chi non usa il mouse.
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onStart(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onStart]);

  return (
    <div className="modal-backdrop">
      <div className="modal welcome" role="dialog" aria-modal="true"
           aria-label="Welcome to MangaRelief">
        <h2>Welcome to MangaRelief</h2>
        <p className="hint">
          A manga panel in, a printable relief out: an STL with the shape and a
          3MF that already carries the filament changes, ready for the slicer.
        </p>

        <ol className="welcome-steps">
          <li>
            <strong>Load a panel.</strong> A scan, a screenshot, a photo of a
            page — up to {limits.max_upload_mb} MB.
          </li>
          <li>
            <strong>Pick the tones.</strong> Click the artwork to choose which
            greys become which layer. The preview is the classification itself,
            so what you see is what gets printed.
          </li>
          <li>
            <strong>Generate.</strong> The model takes over the stage, and both
            files are one click away.
          </li>
        </ol>

        <div className="welcome-facts">
          <p>
            <strong>{plural(limits.anon_generations, "generation")} {windowLabel(limits.window_h)}</strong>{" "}
            without an account, {limits.user_generations} once you sign in,
            which also keeps a history of what you made. What you use now counts
            on your account afterwards, so signing in brings you to{" "}
            {limits.user_generations} in total, not{" "}
            {limits.anon_generations + limits.user_generations}.
          </p>
          <p>
            <strong>Files are deleted after {limits.retention_h} h</strong>, or{" "}
            {limits.post_download_h} h from the first download. Download what you
            want to keep.
          </p>
          <p className="welcome-privacy">
            To count the free generations your browser stores a random
            identifier: it says which browser, never who. No tracking, no
            advertising, nothing sold to anyone. Signing in keeps your email
            address and nothing else.
          </p>
        </div>

        <div className="modal-actions">
          <button type="button" className="link" onClick={onSignIn}>
            sign in first
          </button>
          <button type="button" className="primary" autoFocus onClick={onStart}>
            Start
          </button>
        </div>
      </div>
    </div>
  );
}
