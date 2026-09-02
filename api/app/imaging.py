"""Decoding of uploaded images.

Uploads are untrusted input: decode from memory (never from a path the caller
influences), and refuse anything whose pixel count would blow up RAM long
before the mesh does.
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

from .config import settings

try:  # HEIC/AVIF, same optional support as the desktop app
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optional dependency
    pass

# Pillow's own decompression-bomb guard, aligned with our limit
Image.MAX_IMAGE_PIXELS = settings.max_image_pixels


class ImageTooLarge(ValueError):
    pass


class UndecodableImage(ValueError):
    pass


def decode_upload(data: bytes) -> np.ndarray:
    """Bytes -> RGB array, with OpenCV first and Pillow as the fallback."""
    buf = np.frombuffer(data, dtype=np.uint8)
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    if bgr is not None:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    else:
        try:
            with Image.open(io.BytesIO(data)) as im:
                rgb = np.array(im.convert("RGB"))
        except Image.DecompressionBombError as exc:
            raise ImageTooLarge(str(exc)) from exc
        except Exception as exc:
            raise UndecodableImage("unsupported or corrupt image file") from exc

    h, w = rgb.shape[:2]
    if h * w > settings.max_image_pixels:
        raise ImageTooLarge(
            f"image is {w}x{h} px, over the {settings.max_image_pixels} px limit")
    return rgb
