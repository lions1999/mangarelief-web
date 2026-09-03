/**
 * Tone landmarks for Standard mode: the four greys the relief is built from.
 *
 * The slot being edited lives here rather than in the panel because the panel
 * and the artwork you click are now in two different columns — the swatches in
 * the sidebar, the image on the stage — and they have to agree on which slot a
 * click fills.
 */
import { useState } from "react";
import type { Analysis, JobParams, RGB } from "../types";

export const TONE_LABELS = ["Paper", "Light", "Dark", "Ink"];

const luma = ([r, g, b]: RGB) => Math.round(0.299 * r + 0.587 * g + 0.114 * b);

export interface Tones {
  values: number[] | null;
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
  const [slot, setSlot] = useState(1);   // L1: the one that usually needs help
  const [picked, setPicked] = useState<{ value: number; clamped: number } | null>(null);
  const values = params.sampled_values ?? analysis?.suggested_sampled_values ?? null;

  // The four landmarks must stay ordered light to dark: out of order, the
  // interpolation folds back on itself and the relief loses a level. Rather
  // than ignore a click that breaks the order — which just looks broken — the
  // value is clamped into the space its neighbours leave, and the panel says so.
  const pick = (colour: RGB) => {
    if (!values) return;
    const value = luma(colour);
    const upper = slot === 0 ? 255 : values[slot - 1];
    const lower = slot === values.length - 1 ? 0 : values[slot + 1];
    const clamped = Math.max(lower, Math.min(upper, value));
    setPicked({ value, clamped });
    const next = [...values];
    next[slot] = clamped;
    onChange({ sampled_values: next });
  };

  return {
    values,
    manual: params.sampled_values != null,
    slot,
    select: (i) => {
      setSlot(i);
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
