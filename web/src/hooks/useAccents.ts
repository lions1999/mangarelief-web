/**
 * Spot Colour accents. Same split as useTones: the selected slot is shared
 * between the swatches in the sidebar and the artwork on the stage.
 */
import { useState } from "react";
import type { JobParams, RGB } from "../types";

export interface Accents {
  values: RGB[];
  slot: number;
  select: (slot: number) => void;
  pick: (colour: RGB) => void;
  reset: () => void;
}

export function useAccents(
  params: JobParams,
  onChange: (patch: Partial<JobParams>) => void,
): Accents {
  const [slot, setSlot] = useState(0);
  const values = params.spot_accents;

  const pick = (colour: RGB) => {
    const next = [...values];
    if (slot < next.length) next[slot] = colour;
    else next.push(colour);
    const kept = next.slice(0, 2);
    onChange({ spot_accents: kept, autodetect_accents: kept.length === 0 });
  };

  return {
    values,
    slot,
    select: setSlot,
    pick,
    reset: () => onChange({ spot_accents: [], autodetect_accents: true }),
  };
}
