"""Image analysis and default parameters.

The desktop app derives half of a generation's parameters from the image the
moment it is loaded: white clip from the highlight histogram peak, the two
midtones by K-Means, the colour mode from the halftone percentage, and the Z
change heights from that mode. An HTTP caller has no UI to do that, so the
same logic lives here — deliberately mirroring `Manga3DAppController`, so the
web output matches the desktop output for the same image.

Keep this file in sync with `_refresh_color_mode` / `_compute_auto_z` /
`load_image` in the desktop repo; that is the price of not shipping the UI.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np

from engine.color_utils import suggest_midtones, suggest_spot_accents

# Same defaults as the desktop swatches: [white bg, L1 light, L2 dark, black]
DEFAULT_SAMPLED = [250, 210, 150, 15]
DEFAULT_HALFTONE_THRESHOLD = 10   # ui_main_window: slider_threshold starts at 10


def filtered_gray(image_rgb: np.ndarray) -> np.ndarray:
    """Grayscale + bilateral filter, exactly what the desktop feeds Standard mode."""
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    return cv2.bilateralFilter(gray, d=5, sigmaColor=50, sigmaSpace=50)


def halftone_pct(gray: np.ndarray) -> float:
    """Share of pixels that are neither near-black nor near-white."""
    midtone = np.count_nonzero((gray > 30) & (gray < 225))
    return float(midtone) / float(gray.size) * 100.0


def color_mode_for(pct: float, threshold: int = DEFAULT_HALFTONE_THRESHOLD) -> int:
    """4 = full halftone, 3 = partial (L1 hidden), 2 = pure B/W."""
    if pct >= threshold:
        return 4
    if pct >= (threshold / 2.0):
        return 3
    return 2


def suggest_white_clip(gray: np.ndarray) -> int:
    """Just below the highlight peak, so JPEG noise in the paper is swallowed."""
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
    white_peak_bin = 200 + int(np.argmax(hist[200:]))
    return int(np.clip(white_peak_bin - 15, 180, 250))


def auto_color_changes_z(base_h: float, max_h: float, layer_height: float,
                         color_mode: int) -> List[float]:
    """The desktop's Auto-Z: terraces spread over the available height.

    Mode 2 hides L1/L2 (both 0.0), mode 3 hides L1 — the same convention the
    engine expects, where a 0.0 change height means "this layer is not used".
    """
    available = max_h - base_h

    if color_mode == 2:
        z1 = z2 = 0.0
        z3 = max_h
    elif color_mode == 3:
        z1 = 0.0
        z3 = max_h
        z2 = round((base_h + available / 2.0) / layer_height) * layer_height
        if z2 >= max_h:
            z2 = max_h - layer_height
    else:
        z3 = max_h
        z1 = round((base_h + available / 3.0) / layer_height) * layer_height
        z2 = round((base_h + 2.0 * available / 3.0) / layer_height) * layer_height
        if z2 >= z3:
            z2 = z3 - layer_height
        if z1 >= z2:
            z1 = z2 - layer_height

    return [round(z1, 3), round(z2, 3), round(z3, 3)]


def analyze(image_rgb: np.ndarray, base_h: float = 1.0, max_h: float = 2.4,
            layer_height: float = 0.2,
            halftone_threshold: int = DEFAULT_HALFTONE_THRESHOLD,
            n_accents: int = 2) -> dict:
    """Everything the UI would show after loading an image."""
    gray = filtered_gray(image_rgb)
    pct = halftone_pct(gray)
    mode = color_mode_for(pct, halftone_threshold)
    l1, l2 = suggest_midtones(gray)
    h, w = gray.shape

    return {
        "width": int(w),
        "height": int(h),
        "halftone_pct": round(pct, 2),
        "color_mode": mode,
        "suggested_white_clip": suggest_white_clip(gray),
        "suggested_midtones": (int(l1), int(l2)),
        "suggested_sampled_values": [DEFAULT_SAMPLED[0], int(l1), int(l2), DEFAULT_SAMPLED[3]],
        "suggested_color_changes_z": auto_color_changes_z(base_h, max_h, layer_height, mode),
        "suggested_accents": [tuple(int(c) for c in a)
                              for a in (suggest_spot_accents(image_rgb, n_accents=n_accents) or [])],
    }


def resolve_params(image_rgb: np.ndarray, params) -> Tuple[dict, dict]:
    """Fill in whatever the caller left out, and report what was derived.

    Returns (engine kwargs, analysis) — the analysis is echoed back so the
    caller can see which numbers the server chose on its behalf.
    """
    info = analyze(image_rgb, params.base_h, params.max_h, params.layer_height,
                   params.halftone_threshold,
                   n_accents=max(1, len(params.spot_accents) or 2))

    white_clip = params.white_clip if params.white_clip is not None else info["suggested_white_clip"]
    black_clip = params.black_clip if params.black_clip is not None else 15
    sampled = params.sampled_values or info["suggested_sampled_values"]

    # An explicit colour count wins over the halftone analysis, and the Z
    # heights follow from it: mode 3 hides L1, mode 2 hides L1 and L2, so the
    # suggestion computed for another mode would place the wrong pauses.
    color_mode = params.color_mode or info["color_mode"]
    changes = params.color_changes_z or auto_color_changes_z(
        params.base_h, params.max_h, params.layer_height, color_mode)

    accents: List[Tuple[int, int, int]] = [tuple(a) for a in params.spot_accents]
    if not accents and params.mode.value == "spot_color" and params.autodetect_accents:
        accents = info["suggested_accents"][:2]

    return {
        "max_dim": params.max_dim,
        "base_h": params.base_h,
        "max_h": params.max_h,
        "layer_height": params.layer_height,
        "max_res_cap": params.max_res_cap,
        "white_clip": white_clip,
        "black_clip": black_clip,
        "sampled_values": list(sampled),
        "color_mode": color_mode,
        "color_changes_z": list(changes),
        "spot_accents": accents,
        "spot_coverage": params.spot_coverage,
    }, info
