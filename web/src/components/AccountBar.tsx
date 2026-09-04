/**
 * Who you are and how many generations are left, on one line under the title.
 *
 * A line and not a panel: the first slot in the sidebar belongs to loading the
 * artwork, which is what anyone came here for. But the counter has to stay in
 * sight — finding out you have no generations left *after* loading an image
 * and tuning everything is the worst way to learn it — so it sits at the top,
 * compact, without stealing the scene.
 *
 * The long explanation of what going without an account costs belongs in the
 * welcome message, not here: repeating it at every glance is noise.
 */
import type { Quota } from "../types";
import type { Session } from "../session";

interface Props {
  session: Session | null;
  quota: Quota | null;
  onSignIn: () => void;
  onSignOut: () => void;
  onHistory: () => void;
}

function whenFree(reset: string | null): string {
  if (!reset) return "";
  const h = (new Date(reset).getTime() - Date.now()) / 3_600_000;
  if (h <= 0) return "shortly";
  if (h < 1) return `in ${Math.max(1, Math.round(h * 60))} min`;
  return `in ${Math.round(h)} h`;
}

export default function AccountBar({ session, quota, onSignIn, onSignOut, onHistory }: Props) {
  const unlimited = quota?.limit == null;
  const out = quota && !unlimited ? (quota.remaining ?? 0) <= 0 : false;

  return (
    <div className="account">
      {/* Etichetta a sinistra, valore a destra: e' lo schema che la barra
          laterale usa gia' per ogni impostazione (Long side · 180 mm). Cosi'
          l'occhio trova il numero sempre nello stesso punto, e cambia solo
          quello — non la frase intorno. */}
      <div className="account-row">
        <span>Generations left</span>
        <strong className={out ? "out" : ""}>
          {quota ? (unlimited ? "unlimited" : quota.remaining) : "—"}
        </strong>
      </div>

      {out && quota?.reset_at && (
        <p className="account-note">one frees up {whenFree(quota.reset_at)}</p>
      )}

      {/* L'indirizzo ha una riga sua. E' l'unico pezzo di lunghezza ignota qui
          dentro, e in una riga condivisa e' sempre lui a cedere: prima
          schiacciava "sign out" fino a "sig...", e appena accanto e' comparso
          un secondo comando ha ricominciato a farsi tagliare da solo. Da solo
          su una riga non toglie spazio a nessuno e nessuno ne toglie a lui. */}
      {session?.email && (
        <p className="account-who" title={session.email}>{session.email}</p>
      )}

      <div className="account-links">
        {/* La cronologia esiste solo con un account: senza, il collegamento
            porterebbe a una schermata che dice soltanto di accedere. */}
        {session && (
          <button type="button" className="link" onClick={onHistory}>
            your generations
          </button>
        )}
        <button type="button" className="link"
                onClick={session ? onSignOut : onSignIn}>
          {session ? "sign out" : "sign in"}
        </button>
      </div>
    </div>
  );
}
