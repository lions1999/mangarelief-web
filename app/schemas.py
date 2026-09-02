"""Request/response schemas.

Parameters now arrive from the internet instead of a desktop spinbox, so every
range the UI used to enforce has to be enforced again here. Anything outside a
bound is a 422, never a clamped surprise.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator


class Mode(str, Enum):
    """Modes exposed by the web service (v1: Standard + Spot Color).

    Deckbox and Phone Cover need asset files and physical presets that make no
    sense without the desktop UI; Topographic needs a colour picker. They stay
    in the engine, they are simply not offered here.
    """
    STANDARD = "standard"
    SPOT_COLOR = "spot_color"


RGB = Tuple[int, int, int]


class JobParams(BaseModel):
    """Everything the caller may set for one generation."""

    mode: Mode = Mode.STANDARD

    # --- Physical ---
    max_dim: float = Field(180.0, ge=20.0, le=250.0, description="Long side, mm")
    base_h: float = Field(1.0, ge=0.2, le=10.0, description="Base thickness, mm")
    max_h: float = Field(2.4, ge=0.4, le=20.0, description="Total height, mm")
    layer_height: float = Field(0.2, ge=0.04, le=0.4)
    max_res_cap: int = Field(800, ge=200, le=1600, description="Mesh Quality cap, px")

    # --- Tone calibration (omit to let the server derive them) ---
    white_clip: Optional[int] = Field(None, ge=100, le=255)
    black_clip: Optional[int] = Field(None, ge=0, le=120)
    sampled_values: Optional[List[int]] = None
    color_changes_z: Optional[List[float]] = None
    halftone_threshold: int = Field(10, ge=1, le=100,
                                    description="Halftone %% above which 4-colour mode is used")

    # --- Spot Color ---
    spot_accents: List[RGB] = Field(default_factory=list, max_length=2)
    spot_coverage: int = Field(40, ge=0, le=100)
    autodetect_accents: bool = Field(
        True, description="If no accent is given, detect them from the image")

    @field_validator("sampled_values")
    @classmethod
    def _check_sampled(cls, v):
        if v is None:
            return v
        if len(v) != 4 or not all(0 <= x <= 255 for x in v):
            raise ValueError("sampled_values must be 4 values in 0..255 [white, L1, L2, black]")
        return v

    @field_validator("color_changes_z")
    @classmethod
    def _check_changes(cls, v):
        if v is None:
            return v
        if len(v) != 3 or not all(x >= 0 for x in v):
            raise ValueError("color_changes_z must be 3 non-negative values [z1, z2, z3]")
        return v

    @field_validator("spot_accents")
    @classmethod
    def _check_accents(cls, v):
        for c in v:
            if not all(0 <= ch <= 255 for ch in c):
                raise ValueError("accent channels must be in 0..255")
        return v

    @model_validator(mode="after")
    def _check_heights(self):
        if self.max_h <= self.base_h:
            raise ValueError("max_h must be greater than base_h")
        if (self.max_h - self.base_h) < self.layer_height:
            raise ValueError("max_h - base_h must be at least one layer")
        return self


class Artifact(BaseModel):
    kind: str            # "stl" | "3mf"
    filename: str
    bytes: int
    download_url: str


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    EXPIRED = "expired"


class JobCreated(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str


class JobView(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = 0
    message: str = ""
    mode: str
    created_at: str
    expires_at: Optional[str] = None
    downloaded_at: Optional[str] = None
    duration_s: Optional[float] = None
    error: Optional[str] = None
    artifacts: List[Artifact] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """What the desktop UI computes for you the moment an image is loaded.

    A web client would otherwise have to invent these numbers, so the server
    exposes the same analysis it will use as defaults.
    """
    width: int
    height: int
    halftone_pct: float
    color_mode: int
    suggested_white_clip: int
    suggested_midtones: Tuple[int, int]
    suggested_sampled_values: List[int]
    suggested_color_changes_z: List[float]
    suggested_accents: List[RGB]
