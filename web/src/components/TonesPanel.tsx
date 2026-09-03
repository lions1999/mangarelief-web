/**
 * Tone landmarks for Standard mode: the four greys the relief is built from.
 *
 * The desktop app has always let you sample these off the artwork and see the
 * result, because "auto" gets the midtones wrong on any panel with a large flat
 * grey — and a wrong midtone puts a whole face on the wrong filament. The
 * preview shows which bobbin covers which area, not a smooth ramp.
 */
import { useState } from "react";
import PickableImage from "./PickableImage";
import { useMockup } from "../hooks/useMockup";
import type { Analysis, JobParams, RGB } from "../types";

interface Props {
  file: File;
  params: JobParams;
  analysis: Analysis | null;
  disabled: boolean;
  onChange: (patch: Partial<JobParams>) => void;
}

const LABELS = ["Paper", "Light", "Dark", "Ink"];

const luma = ([r, g, b]: RGB) => Math.round(0.299 * r + 0.587 * g + 0.114 * b);
const greyHex = (v: number) => "#" + v.toString(16).padStart(2, "0").repeat(3);

export default function TonesPanel({ file, params, analysis, disabled, onChange }: Props) {
  const [slot, setSlot] = useState(1);   // L1: the one that usually needs help
  const [picked, setPicked] = useState<{ value: number; clamped: number } | null>(null);
  const tones = params.sampled_values ?? analysis?.suggested_sampled_values ?? null;
  const changes = params.color_changes_z ?? analysis?.suggested_color_changes_z ?? null;
  const manual = params.sampled_values != null;

  const { url, busy, error } = useMockup(
    file,
    { mode: "standard", sampled_values: tones ?? undefined, color_changes_z: changes ?? undefined },
    [tones?.join(), changes?.join()],
  );

  if (!tones) return null;

  // The four landmarks must stay ordered light to dark: out of order, the
  // interpolation folds back on itself and the relief loses a level. Rather
  // than ignore a click that breaks the order — which just looks broken — the
  // value is clamped into the space its neighbours leave, and the panel says so.
  const setTone = (value: number) => {
    const upper = slot === 0 ? 255 : tones[slot - 1];
    const lower = slot === tones.length - 1 ? 0 : tones[slot + 1];
    const clamped = Math.max(lower, Math.min(upper, value));
    setPicked({ value, clamped });
    const next = [...tones];
    next[slot] = clamped;
    onChange({ sampled_values: next });
  };

  return (
    <section className="panel">
      <h2>Tones</h2>
      <p className="hint">
        The four greys the relief is built from. Click the artwork to sample one
        into the selected slot — useful when a large flat area lands on the
        wrong level.
      </p>

      <div className="accents">
        {tones.map((v, i) => (
          <button
            key={i}
            type="button"
            disabled={disabled}
            className={`swatch${slot === i ? " active" : ""}`}
            style={{ background: greyHex(v) }}
            onClick={() => {
              setSlot(i);
              setPicked(null);
            }}
            title={`${LABELS[i]} — ${v}`}
          />
        ))}
        {manual && (
          <button
            type="button"
            className="link"
            disabled={disabled}
            onClick={() => {
              onChange({ sampled_values: null });
              setPicked(null);
            }}
          >
            back to auto
          </button>
        )}
      </div>
      <p className="hint">
        {LABELS[slot]} · {tones[slot]}
        {manual ? "" : " · detected automatically"}
        {picked && picked.value !== picked.clamped && (
          <>
            {" "}— sampled {picked.value}, kept at {picked.clamped} so the four
            tones stay in order
          </>
        )}
      </p>

      <div className="mockup">
        <figure>
          <PickableImage file={file} disabled={disabled} onPick={(c) => setTone(luma(c))} />
          <figcaption>Source — click to sample</figcaption>
        </figure>
        <figure>
          {url ? <img src={url} alt="Tone preview" /> : <div className="placeholder" />}
          <figcaption>{busy ? "Updating preview…" : "One shade per filament"}</figcaption>
        </figure>
      </div>
      {error && <p className="field-error">{error}</p>}
    </section>
  );
}
