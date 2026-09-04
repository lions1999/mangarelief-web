/**
 * Le poche formule con cui si scrivono i numeri del servizio.
 *
 * Stanno insieme perche' devono suonare uguali ovunque: la finestra della
 * quota compare nel benvenuto e sotto il pulsante Genera, e vederla scritta in
 * due modi diversi nella stessa pagina fa dubitare che sia lo stesso limite.
 */

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** 24 ore sono "un giorno" per chi legge; qualunque altra finestra si dice in ore. */
export const windowLabel = (h: number) => (h === 24 ? "a day" : `every ${h} h`);
