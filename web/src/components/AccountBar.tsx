/**
 * Chi sei e quante generazioni ti restano, in una riga sotto il titolo.
 *
 * Una riga e non un pannello: il primo posto della barra laterale spetta al
 * caricamento dell'immagine, che e' cio' per cui si e' arrivati qui. Ma il
 * contatore deve restare sempre in vista — scoprire di non avere piu'
 * generazioni *dopo* aver caricato un'immagine e regolato tutto e' il modo
 * peggiore di saperlo — quindi sta in alto, compatto, senza rubare la scena.
 *
 * La spiegazione lunga di cosa comporta non avere un account sta nel messaggio
 * di benvenuto, non qui: ripeterla a ogni sguardo e' rumore.
 */
import type { Quota } from "../types";
import type { Session } from "../session";

interface Props {
  session: Session | null;
  quota: Quota | null;
  onSignIn: () => void;
  onSignOut: () => void;
}

function whenFree(reset: string | null): string {
  if (!reset) return "";
  const h = (new Date(reset).getTime() - Date.now()) / 3_600_000;
  if (h <= 0) return "a momenti";
  if (h < 1) return `fra ${Math.max(1, Math.round(h * 60))} min`;
  return `fra ${Math.round(h)} ore`;
}

export default function AccountBar({ session, quota, onSignIn, onSignOut }: Props) {
  const senzaTetto = quota?.limit == null;
  const out = quota && !senzaTetto ? (quota.remaining ?? 0) <= 0 : false;

  return (
    <div className="account-bar">
      <span className={`quota${out ? " out" : ""}`}>
        {quota ? (
          senzaTetto ? (
            <>generazioni <strong>illimitate</strong></>
          ) : (
            <>
              <strong>{quota.remaining}</strong> di {quota.limit} generazioni
              {out && quota.reset_at && <em>una si libera {whenFree(quota.reset_at)}</em>}
            </>
          )
        ) : (
          <span className="hint">…</span>
        )}
      </span>
      {session ? (
        <span className="account-who" title={session.email ?? ""}>
          {session.email}
          <button type="button" className="link" onClick={onSignOut}>esci</button>
        </span>
      ) : (
        <button type="button" className="link" onClick={onSignIn}>accedi</button>
      )}
    </div>
  );
}
