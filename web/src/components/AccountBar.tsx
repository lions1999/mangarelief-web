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
}

function whenFree(reset: string | null): string {
  if (!reset) return "";
  const h = (new Date(reset).getTime() - Date.now()) / 3_600_000;
  if (h <= 0) return "shortly";
  if (h < 1) return `in ${Math.max(1, Math.round(h * 60))} min`;
  return `in ${Math.round(h)} h`;
}

export default function AccountBar({ session, quota, onSignIn, onSignOut }: Props) {
  const senzaTetto = quota?.limit == null;
  const out = quota && !senzaTetto ? (quota.remaining ?? 0) <= 0 : false;

  return (
    <div className="account-bar">
      <span className={`quota${out ? " out" : ""}`}>
        {quota ? (
          senzaTetto ? (
            <><strong>unlimited</strong> generations</>
          ) : (
            <>
              <strong>{quota.remaining}</strong> of {quota.limit} generations
              {out && quota.reset_at && <em>one frees up {whenFree(quota.reset_at)}</em>}
            </>
          )
        ) : (
          <span className="hint">…</span>
        )}
      </span>
      {session ? (
        <span className="account-who" title={session.email ?? ""}>
          {session.email}
          <button type="button" className="link" onClick={onSignOut}>sign out</button>
        </span>
      ) : (
        <button type="button" className="link" onClick={onSignIn}>sign in</button>
      )}
    </div>
  );
}
