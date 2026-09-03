/**
 * Tone landmarks for Standard mode: the greys the relief is calibrated from.
 *
 * The slot being edited lives here rather than in the panel because the panel
 * and the artwork you click are now in two different columns — the swatches in
 * the sidebar, the image on the stage — and they have to agree on which slot a
 * click fills.
 */
import { useState } from "react";
import type { Analysis, JobParams, RGB } from "../types";

export const TONE_LABELS = ["Paper", "Light", "Dark", "Ink"];

/**
 * Which of the four the engine actually reads, per colour count — measured
 * against the pipeline, not assumed. Two colours threshold the image at
 * (Paper + Ink) / 2 and never look at the midtones; three snap to Dark; four
 * snap to Light and Dark and take their midpoint. Showing the inert ones is
 * an invitation to spend a minute calibrating a swatch that does nothing.
 */
const ACTIVE: Record<number, number[]> = { 2: [0, 3], 3: [2], 4: [1, 2] };

const luma = ([r, g, b]: RGB) => Math.round(0.299 * r + 0.587 * g + 0.114 * b);

export interface Tones {
  values: number[] | null;
  /** Indices into `values` that this colour count actually uses. */
  active: number[];
  colours: number;
  manual: boolean;
  slot: number;
  select: (slot: number) => void;
  /** What the last click sampled, and what it was kept at. */
  picked: { value: number; clamped: number } | null;
  pick: (colour: RGB) => void;
  reset: () => void;
}

export function useTones(
  params: JobParams,
  analysis: Analysis | null,
  onChange: (patch: Partial<JobParams>) => void,
): Tones {
  const [wanted, setWanted] = useState(2);
  const [picked, setPicked] = useState<{ value: number; clamped: number } | null>(null);

  const values = params.sampled_values ?? analysis?.suggested_sampled_values ?? null;
  const colours = params.color_mode ?? analysis?.color_mode ?? 4;
  const active = ACTIVE[colours] ?? ACTIVE[4];
  // Changing the colour count can retire the selected slot; fall back to the
  // first one that still does something rather than leaving a dead selection.
  const slot = active.includes(wanted) ? wanted : active[0];

  // The tones in play must stay ordered light to dark: out of order, the
  // interpolation folds back on itself and the relief loses a level. Rather
  // than ignore a click that breaks the order — which just looks broken — the
  // value is clamped into the space its neighbours leave, and the panel says
  // so. Only the *active* slots constrain each other: at two colours, Paper is
  // bounded by Ink alone, and the midtones it sits above are irrelevant.
  const pick = (colour: RGB) => {
    if (!values) return;
    const value = luma(colour);
    const pos = active.indexOf(slot);
    const upper = pos === 0 ? 255 : values[active[pos - 1]];
    const lower = pos === active.length - 1 ? 0 : values[active[pos + 1]];
    const clamped = Math.max(lower, Math.min(upper, value));
    setPicked({ value, clamped });
    const next = [...values];
    next[slot] = clamped;
    onChange({ sampled_values: next });
  };

  return {
    values,
    active,
    colours,
    manual: params.sampled_values != null,
    slot,
    select: (i) => {
      setWanted(i);
      setPicked(null);
    },
    picked,
    pick,
    reset: () => {
      onChange({ sampled_values: null });
      setPicked(null);
    },
  };
}
