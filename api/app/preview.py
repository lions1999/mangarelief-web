"""L'immagine "come verra' stampato": una sola implementazione, due usi.

Il mockup nasce per la barra di regolazione — si chiama a ogni movimento di
cursore e si guarda mentre si sceglie — ma e' anche la figura giusta per la
cronologia. La ragione e' che distingue due generazioni della *stessa* tavola:
lo stesso pannello a 2, 3 e 4 colori darebbe tre miniature identiche se si
usasse l'immagine caricata, mentre col mockup si riconoscono a colpo d'occhio,
perche' il mockup *e'* la differenza fra le tre.

Costa quasi niente da conservare: sono quattro colori piatti, e a 320px un webp
senza perdita sta in 7 KB — un quindicesimo dell'immagine sorgente.

Sta qui e non dentro l'endpoint perche' la cronologia deve mostrare esattamente
cio' che l'anteprima mostrava: due implementazioni che divergono di un ritocco
sono due implementazioni che prima o poi si contraddicono in faccia a chi
guarda.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

from engine import GenerationMode, GenerationParams, prepare_source_image, standard_heightmap
from engine.color_utils import classify_spot_pixels, downsample_for_analysis

from .analysis import analyze, bw_ambiguity, engine_input, resolve_params

# Grande abbastanza da giudicare un accento, piccola abbastanza da essere
# ricalcolata a ogni movimento del cursore.
MOCKUP_MAX_PX = 700


class NoAccents(ValueError):
    """Spot color senza accenti: non c'e' niente da mostrare."""


def _band_tones(sampled, color_mode: int):
    """The grey each printed band shows, light at the bottom, dark on top.

    Mirrors which levels the desktop selector hides: 4 colours use all four
    sampled tones, 3 drop L1, 2 keep only paper and ink.
    """
    if color_mode >= 4:
        return list(sampled)
    if color_mode == 3:
        return [sampled[0], sampled[2], sampled[3]]
    return [sampled[0], sampled[3]]


def render(rgb: np.ndarray, p, max_px: int = MOCKUP_MAX_PX) -> Tuple[np.ndarray, Dict[str, Any]]:
    """L'anteprima come array RGB, piu' le intestazioni che dipendono dal
    calcolo (l'ambiguita' del taglio a due colori).

    Solleva `NoAccents` quando spot color non ha accenti da usare.
    """
    extra: Dict[str, Any] = {}
    small = downsample_for_analysis(rgb, max_px)

    if p.mode.value == "spot_color":
        accents = [tuple(a) for a in p.spot_accents]
        if not accents and p.autodetect_accents:
            accents = analyze(rgb, n_accents=2)["suggested_accents"][:2]
        if not accents:
            raise NoAccents("spot_color needs at least one accent colour to preview")
        white_clip = p.white_clip if p.white_clip is not None else 235
        black_clip = p.black_clip if p.black_clip is not None else 15
        palette, idx = classify_spot_pixels(small, accents, coverage=p.spot_coverage,
                                            white_clip=white_clip, black_clip=black_clip)
        return np.array(palette, dtype=np.uint8)[idx], extra

    # Standard: ogni pixel dipinto con il tono in cui stampera' davvero,
    # eseguendo gli stessi due passi del motore, sullo stesso ingresso, alla
    # stessa risoluzione. Qui non si approssima la mesh.
    #
    # Una prima versione saltava prepare_source_image e leggeva la mappa di
    # altezza dal grigio filtrato. E' un'altra figura: la posterizzazione e'
    # cio' che assegna un pixel a una bobina, e con due colori e' tutta la
    # modalita' — l'anteprima segnava 39,9% di inchiostro mentre la mesh
    # passava da 38,5% a 0% man mano che si calibravano i campioni.
    engine_kwargs, _ = resolve_params(rgb, p)
    params = GenerationParams(mode=GenerationMode.STANDARD, **engine_kwargs)
    source = engine_input(rgb, "standard")
    z = standard_heightmap(prepare_source_image(source, params), params)
    if params.color_mode == 2 and params.bw_coverage is not None:
        # Viaggia con l'anteprima perche' dipende dal taglio che si sta
        # trascinando adesso: un numero calcolato una volta al caricamento
        # descriverebbe un'altra impostazione.
        extra["X-MangaRelief-Ambiguous"] = "%.4f" % bw_ambiguity(
            source, params.max_dim, params.max_res_cap, params.bw_coverage)

    changes = [c for c in params.color_changes_z if c > 0]
    tones = _band_tones(params.sampled_values, params.color_mode)

    # Un pixel stampa nel colore caricato quando si raggiunge la sua
    # superficie, quindi la sua banda e' quanti cambi stanno alla sua altezza o
    # sotto. Contano tutti, l'ultimo compreso: lasciarlo fuori — come faceva
    # una prima versione — lascia il colore dell'inchiostro inutilizzato e, con
    # due colori, dipinge tutta l'immagine color carta.
    band = np.zeros(z.shape, dtype=np.int32)
    for c in changes:
        band += (z >= c - 1e-9).astype(np.int32)
    painted = np.array(tones, dtype=np.uint8)[np.clip(band, 0, len(tones) - 1)]

    # Ridotta col nearest: una media pesata inventerebbe toni che nessuna
    # bobina stampa.
    ph, pw = painted.shape
    scale = max_px / max(ph, pw)
    if scale < 1.0:
        painted = cv2.resize(painted, (max(1, int(pw * scale)), max(1, int(ph * scale))),
                             interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(painted, cv2.COLOR_GRAY2RGB), extra


def encode_png(preview: np.ndarray) -> Optional[bytes]:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(preview, cv2.COLOR_RGB2BGR))
    return buf.tobytes() if ok else None
