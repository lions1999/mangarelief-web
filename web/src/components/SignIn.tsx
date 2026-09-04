/**
 * Signing in, in two steps: the address, then the code that arrives by email.
 *
 * No password, so nothing to remember, reset or have stolen. A code and not a
 * link because a link opens a new tab, and whoever had already loaded artwork
 * and tuned the settings would lose all of it: here everything stays where it
 * is, and signing in returns you to your own work.
 *
 * Nothing here states how many digits the code has. That number is a Supabase
 * setting, and hard-coding it in the copy is how the footer ended up claiming
 * "5 per hour" months after the limit had changed.
 */
import { useState } from "react";
import { requestCode, verifyCode } from "../api";
import { setSession } from "../session";

interface Props {
  onDone: (linked: number) => void;
  onCancel: () => void;
}

/** Supabase permette codici da 6 a 10 cifre: il campo li accetta tutti. */
const MAX_CODE = 10;

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
      setError(err instanceof Error ? err.message : "could not send the code");
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
      setError(err instanceof Error ? err.message : "that code is not valid");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        {step === "email" ? (
          <form onSubmit={send}>
            <h2>Sign in</h2>
            <p className="hint">
              We email you a short code. No password: nothing to remember,
              and you stay on this page without losing the work in progress.
            </p>
            <label className="field">
              <span>Your email address</span>
              <input
                type="email"
                autoFocus
                required
                value={email}
                disabled={busy}
                placeholder="you@example.com"
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            <p className="hint">
              Disposable addresses are not accepted: generations are counted
              per account, and throwaway mailboxes exist only to get around
              that count.
            </p>
            {error && <p className="field-error">{error}</p>}
            <div className="modal-actions">
              <button type="button" className="link" onClick={onCancel} disabled={busy}>
                cancel
              </button>
              <button className="primary" type="submit" disabled={busy || !email.trim()}>
                {busy ? "Sending…" : "Email me a code"}
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={confirm}>
            <h2>Check your inbox</h2>
            <p className="hint">
              We sent a code to <strong>{email}</strong>. If you cannot find
              it, look in the spam folder.
            </p>
            <label className="field">
              <span>Code</span>
              <input
                className="code-input"
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus
                required
                value={code}
                disabled={busy}
                placeholder="——————"
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, MAX_CODE))}
              />
            </label>
            {error && <p className="field-error">{error}</p>}
            <div className="modal-actions">
              <button type="button" className="link" disabled={busy}
                      onClick={() => { setStep("email"); setCode(""); setError(""); }}>
                use another address
              </button>
              <button className="primary" type="submit" disabled={busy || code.length < 4}>
                {busy ? "Checking…" : "Sign in"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
