/**
 * Chi sei, da quale browser, e quante generazioni ti restano.
 *
 * La sessione sta in localStorage e non in memoria: chiudere la scheda a meta'
 * lavoro non deve costare un nuovo accesso. L'access token dura poco, quindi
 * ogni chiamata passa da `authHeaders`, che lo rinnova quando sta per scadere
 * — silenziosamente, senza rimandare nessuno alla schermata di accesso mentre
 * sta generando.
 *
 * L'identificativo del dispositivo e' casuale e non contiene nulla di
 * personale: identifica un'installazione del browser, non una persona. Serve
 * a contare le prove gratuite senza usare l'indirizzo IP, che sotto CGNAT e'
 * condiviso da migliaia di utenti — contarlo negherebbe la prova a chi arriva
 * secondo. Rientra fra gli strumenti tecnici necessari, quindi non richiede un
 * banner di consenso, ma il messaggio di benvenuto lo dice comunque.
 */

const SESSION_KEY = "mangarelief.session";
const DEVICE_KEY = "mangarelief.device";
/** Rinnova con un margine: un token che scade a meta' upload è un errore inutile. */
const REFRESH_MARGIN_S = 120;

export interface Session {
  access_token: string;
  refresh_token: string;
  expires_at?: number | null;
  email?: string | null;
  user_id: string;
}

function read<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;   // modalità privata, storage pieno, JSON rotto: si prosegue da anonimi
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* non poter ricordare la sessione non deve impedire di usare il sito */
  }
}

export function deviceId(): string {
  let id = read<string>(DEVICE_KEY);
  if (!id) {
    id = (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`);
    write(DEVICE_KEY, id);
  }
  return id;
}

export function getSession(): Session | null {
  return read<Session>(SESSION_KEY);
}

export function setSession(s: Session | null): void {
  if (s) write(SESSION_KEY, s);
  else {
    try {
      localStorage.removeItem(SESSION_KEY);
    } catch { /* vedi sopra */ }
  }
  listeners.forEach((fn) => fn(s));
}

type Listener = (s: Session | null) => void;
const listeners = new Set<Listener>();

/** Notifica i componenti quando si entra o si esce. */
export function onSessionChange(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function expiringSoon(s: Session): boolean {
  if (!s.expires_at) return false;
  return s.expires_at - Date.now() / 1000 < REFRESH_MARGIN_S;
}
