/**
 * Accesso in due passi: l'indirizzo, poi il codice che arriva per email.
 *
 * Nessuna password, quindi niente da ricordare, reimpostare o farsi rubare.
 * Un codice e non un link perche' il link aprirebbe una scheda nuova e chi ha
 * gia' caricato l'immagine e regolato i parametri li perderebbe: qui resta
 * tutto dov'e', e a fine accesso si torna esattamente al proprio lavoro.
 */
import { useState } from "react";
import { requestCode, verifyCode } from "../api";
import { setSession } from "../session";

interface Props {
  onDone: (linked: number) => void;
  onCancel: () => void;
}

export default function SignIn({ onDone, onCancel }: Props) {
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await requestCode(email.trim());
      setStep("code");
    } catch (err) {
      setError(err instanceof Error ? err.message : "non è stato possibile inviare il codice");
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const s = await verifyCode(email.trim(), code.trim());
      setSession(s);
      onDone(s.linked ?? 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "codice non valido");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        {step === "email" ? (
          <form onSubmit={send}>
            <h2>Accedi</h2>
            <p className="hint">
              Ti mandiamo un codice di sei cifre. Nessuna password: non c'è
              niente da ricordare, e resti su questa pagina senza perdere il
              lavoro in corso.
            </p>
            <label className="field">
              <span>Il tuo indirizzo email</span>
              <input
                type="email"
                autoFocus
                required
                value={email}
                disabled={busy}
                placeholder="tu@esempio.it"
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            <p className="hint">
              Gli indirizzi temporanei non sono accettati: le generazioni sono
              contate per account, e le caselle usa-e-getta servono solo ad
              aggirare quel conteggio.
            </p>
            {error && <p className="field-error">{error}</p>}
            <div className="modal-actions">
              <button type="button" className="link" onClick={onCancel} disabled={busy}>
                annulla
              </button>
              <button className="primary" type="submit" disabled={busy || !email.trim()}>
                {busy ? "Invio…" : "Mandami il codice"}
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={confirm}>
            <h2>Controlla la posta</h2>
            <p className="hint">
              Abbiamo mandato un codice di sei cifre a <strong>{email}</strong>.
              Se non lo trovi, guarda nello spam.
            </p>
            <label className="field">
              <span>Codice</span>
              <input
                className="code-input"
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus
                required
                value={code}
                disabled={busy}
                placeholder="123456"
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 8))}
              />
            </label>
            {error && <p className="field-error">{error}</p>}
            <div className="modal-actions">
              <button type="button" className="link" disabled={busy}
                      onClick={() => { setStep("email"); setCode(""); setError(""); }}>
                cambia indirizzo
              </button>
              <button className="primary" type="submit" disabled={busy || code.length < 4}>
                {busy ? "Verifico…" : "Entra"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
