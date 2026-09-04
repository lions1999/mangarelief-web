/**
 * Accent slots and coverage, as sidebar controls.
 *
 * Coverage is impossible to judge from a number: it decides how far an accent
 * spreads into muted tones, and the only way to set it is to watch the preview
 * on the stage change as you drag.
 */
import Info from "./Info";
import type { Accents } from "../hooks/useAccents";
import type { JobParams, RGB } from "../types";

interface Props {
  accents: Accents;
  params: JobParams;
  suggested: RGB[];
  disabled: boolean;
  onChange: (patch: Partial<JobParams>) => void;
}

const hex = ([r, g, b]: RGB) =>
  "#" + [r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("");

export default function SpotPanel({ accents, params, suggested, disabled, onChange }: Props) {
  const values = accents.values;

  return (
    <section className="panel">
      <h2>
        Accents
        <Info label="accents">
          <p>
            A silkscreen, not a reproduction: one or two colours carry the whole
            image, and everything else is forced to white paper or black
            linework. Two spools plus black and white, and the panel reads.
          </p>
          <p>
            Coverage decides how far an accent reaches: low keeps it on the
            vivid pixels only, high pulls in the muted shades around them. It is
            not a number you can judge in the abstract — drag it and watch the
            preview.
          </p>
        </Info>
      </h2>
      <p className="hint">
        Click the artwork to pick a colour into the selected slot.
      </p>

      <div className="accents">
        {[0, 1].map((i) => {
          const colour = values[i];
          return (
            <button
              key={i}
              type="button"
              disabled={disabled}
              className={`swatch${accents.slot === i ? " active" : ""}`}
              style={colour ? { background: hex(colour) } : undefined}
              onClick={() => accents.select(i)}
              title={colour ? hex(colour) : "empty slot"}
            >
              {!colour && <span>+</span>}
            </button>
          );
        })}
        {values.length > 0 && (
          <button type="button" className="link" disabled={disabled} onClick={accents.reset}>
            reset to detected
          </button>
        )}
      </div>

      {values.length === 0 && suggested.length > 0 && (
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
    </section>
  );
}
