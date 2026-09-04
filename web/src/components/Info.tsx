/**
 * La "i" accanto a un titolo: la spiegazione c'e', ma solo per chi la chiede.
 *
 * La barra laterale spiegava ogni controllo sotto il controllo stesso. Va bene
 * la prima volta e diventa rumore subito dopo: chi ha gia' capito che cosa fa
 * la copertura si ritrova quelle quattro righe sotto gli occhi a ogni
 * generazione, e per arrivare al comando successivo deve scorrerle. Qui sotto
 * il controllo resta solo cio' che *cambia* — il conteggio, il valore
 * campionato, l'avviso — e il resto sta dietro la "i".
 *
 * Si apre con un clic e non al passaggio del mouse: su un telefono non esiste
 * un passaggio del mouse, e una spiegazione raggiungibile solo col mouse e'
 * una spiegazione che meta' delle persone non legge mai.
 */
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** Aprirne una chiude le altre: due riquadri aperti si sovrappongono e basta. */
const APERTA = "mangarelief:info-aperta";

interface Props {
  /** Di cosa parla: finisce nell'etichetta per chi naviga a voce. */
  label: string;
  children: ReactNode;
}

export default function Info({ label, children }: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [dove, setDove] = useState<{ top: number; left: number; maxHeight: number } | null>(null);
  const box = useRef<HTMLSpanElement>(null);
  const pop = useRef<HTMLSpanElement>(null);

  /**
   * Dove va il riquadro. Sta fuori dal flusso (`fixed`), quindi la posizione
   * e' tutta qui.
   *
   * Appeso al bottone veniva tagliato in due modi: di lato, perche' il bottone
   * sta dove finisce il titolo e sotto "Size and print" quel punto e' troppo a
   * destra per farci stare 262px; e in basso, perche' la colonna delle
   * impostazioni ha il proprio scorrimento e ritaglia cio' che sporge.
   */
  const colloca = useCallback(() => {
    if (!pop.current || !box.current) return;
    const margine = 12;
    const bordo = (box.current.closest(".side") ?? document.body).getBoundingClientRect();
    const qui = box.current.getBoundingClientRect();
    const r = pop.current.getBoundingClientRect();

    const left = Math.max(bordo.left + margine,
                          Math.min(qui.left - 8, bordo.right - r.width - margine));

    // Sotto se ci sta, sopra altrimenti — e se non ci sta da nessuna delle due
    // parti si prende il lato piu' largo e si lascia scorrere il testo dentro
    // al riquadro. L'alternativa, incollarlo al bordo dello schermo, lo faceva
    // finire *sopra* il suo stesso bottone: sembra un altro elemento, non la
    // spiegazione di quello.
    const spazioSotto = window.innerHeight - qui.bottom - 8 - margine;
    const spazioSopra = qui.top - 8 - margine;
    // L'altezza naturale, non quella gia' tagliata dal calcolo precedente.
    const naturale = pop.current.scrollHeight;
    const sotto = naturale <= spazioSotto || spazioSotto >= spazioSopra;
    const maxHeight = Math.max(sotto ? spazioSotto : spazioSopra, 120);
    const top = sotto ? qui.bottom + 8 : qui.top - 8 - Math.min(naturale, maxHeight);
    setDove({ top, left, maxHeight });
  }, []);

  useLayoutEffect(() => { if (open) colloca(); }, [open, colloca]);

  useEffect(() => {
    if (!open) return;
    const fuori = (e: Event) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const altra = (e: Event) => { if ((e as CustomEvent).detail !== id) setOpen(false); };
    // Seguire, non chiudere: chi apre una "i" mezza fuori vista fa scorrere la
    // colonna al browser, e un riquadro che si chiude da solo un istante dopo
    // averlo aperto sembra rotto. `capture` perche' lo scorrimento di una
    // colonna interna non risale fino a window.
    let frame = 0;
    const segui = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(colloca);
    };
    document.addEventListener("mousedown", fuori);
    window.addEventListener("keydown", esc);
    window.addEventListener(APERTA, altra);
    window.addEventListener("scroll", segui, true);
    window.addEventListener("resize", segui);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("mousedown", fuori);
      window.removeEventListener("keydown", esc);
      window.removeEventListener(APERTA, altra);
      window.removeEventListener("scroll", segui, true);
      window.removeEventListener("resize", segui);
    };
  }, [open, id, colloca]);

  return (
    <span className="info" ref={box}>
      <button
        type="button"
        className="info-btn"
        aria-expanded={open}
        aria-label={`About ${label}`}
        onClick={() => {
          const prossimo = !open;
          setOpen(prossimo);
          if (prossimo) window.dispatchEvent(new CustomEvent(APERTA, { detail: id }));
        }}
      >
        {/* La "i" e' disegnata dal CSS: dentro il bottone finirebbe nel testo
            del titolo, che diventerebbe "Tonesi" — per chi copia la pagina,
            per chi la legge a voce e per chiunque ci cerchi dentro. Il nome
            accessibile e' l'aria-label qui sopra. */}
      </button>
      {open && (
        <span
          className="info-pop"
          role="tooltip"
          ref={pop}
          // Prima della misura sta fuori vista: un lampo nell'angolo in alto a
          // sinistra e poi un salto al suo posto si vede, ed e' brutto.
          style={dove ?? { visibility: "hidden", top: 0, left: 0 }}
        >
          {children}
        </span>
      )}
    </span>
  );
}
