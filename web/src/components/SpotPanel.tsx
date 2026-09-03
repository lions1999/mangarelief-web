/**
 * Accent picking + coverage, with the live 2D mockup beside them.
 *
 * Coverage is impossible to judge from a number: it decides how far an accent
 * spreads into muted tones, and the only way to set it is to see the result.
 * The preview call is debounced (see useMockup) because it costs a server
 * round-trip today — the one call phase 4 replaces with a local classifier.
 */
import { useState } from "react";
import PickableImage from "./PickableImage";
import { useMockup } from "../hooks/useMockup";
import type { JobParams, RGB } from "../types";

interface Props {
  file: File;
  params: JobParams;
  suggested: RGB[];
  disabled: boolean;
  onChange: (patch: Partial<JobParams>) => void;
}

const hex = ([r, g, b]: RGB) =>
  "#" + [r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("");

export default function SpotPanel({ file, params, suggested, disabled, onChange }: Props) {
  const [slot, setSlot] = useState(0);
  const { url: preview, busy, error: problem } = useMockup(
    file,
    {
      mode: "spot_color",
      spot_accents: params.spot_accents,
      spot_coverage: params.spot_coverage,
      autodetect_accents: params.spot_accents.length === 0,
    },
    [params.spot_accents.map((a) => a.join()).join("|"), params.spot_coverage],
  );

  const accents = params.spot_accents;
  const setAccent = (index: number, colour: RGB | null) => {
    const next = [...accents];
    if (colour === null) next.splice(index, 1);
    else if (index < next.length) next[index] = colour;
    else next.push(colour);
    onChange({ spot_accents: next.slice(0, 2), autodetect_accents: next.length === 0 });
  };

  return (
    <section className="panel">
      <h2>Accents</h2>
      <p className="hint">
        Everything that is not an accent is printed white or black. Click the
        artwork to pick a colour into the selected slot.
      </p>

      <div className="accents">
        {[0, 1].map((i) => {
          const colour = accents[i];
          return (
            <button
              key={i}
              type="button"
              disabled={disabled}
              className={`swatch${slot === i ? " active" : ""}`}
              style={colour ? { background: hex(colour) } : undefined}
              onClick={() => setSlot(i)}
              title={colour ? hex(colour) : "empty slot"}
            >
              {!colour && <span>+</span>}
            </button>
          );
        })}
        {accents.length > 0 && (
          <button
            type="button"
            className="link"
            disabled={disabled}
            onClick={() => onChange({ spot_accents: [], autodetect_accents: true })}
          >
            reset to detected
          </button>
        )}
      </div>

      {accents.length === 0 && suggested.length > 0 && (
        <p className="hint">
          Using the colours detected in the image:{" "}
          {suggested.slice(0, 2).map((c) => (
            <span key={hex(c)} className="chip" style={{ background: hex(c) }} />
          ))}
        </p>
      )}

      <label className="field">
        <span>
          Coverage <em>{params.spot_coverage}%</em>
        </span>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={params.spot_coverage}
          disabled={disabled}
          onChange={(e) => onChange({ spot_coverage: Number(e.target.value) })}
        />
      </label>
      <p className="hint">
        Low keeps only vivid pixels on the accent; high pulls in muted shades too.
      </p>

      <div className="mockup">
        <figure>
          <PickableImage file={file} disabled={disabled}
                         onPick={(c) => setAccent(slot, c)} />
          <figcaption>Source — click to pick</figcaption>
        </figure>
        <figure>
          {preview ? <img src={preview} alt="Spot colour preview" /> : <div className="placeholder" />}
          <figcaption>{busy ? "Updating preview…" : "How it will print"}</figcaption>
        </figure>
      </div>
      {problem && <p className="field-error">{problem}</p>}
    </section>
  );
}
