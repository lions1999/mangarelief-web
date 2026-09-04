/**
 * What kind of relief, and how many filaments it costs to print.
 *
 * These two belong together and above everything else: the mode decides which
 * colour controls exist at all, and the count is the first thing a visitor
 * weighs, because each extra colour is one more bobbin to own and one more
 * pause to stand through.
 */
import Info from "./Info";
import type { Analysis, JobParams, Mode } from "../types";

interface Props {
  params: JobParams;
  analysis: Analysis | null;
  disabled: boolean;
  /** From the live preview: share of the artwork that flips with a nudge of the two-colour cut. */
  ambiguous: number | null;
  onChange: (patch: Partial<JobParams>) => void;
}

/** Above this, two colours is the wrong tool for the artwork, not a setting to tune. */
const AMBIGUOUS_WARN = 0.10;

const MODES: { value: Mode; label: string; blurb: string }[] = [
  {
    value: "standard",
    label: "Standard relief",
    blurb: "Grayscale terraces. The colour count follows the halftone density of the artwork.",
  },
  {
    value: "spot_color",
    label: "Spot colour",
    blurb: "Silkscreen look: white base, one or two accents, black linework on top.",
  },
];

export default function LookPanel({ params, analysis, disabled, ambiguous, onChange }: Props) {
  const colours = params.color_mode ?? analysis?.color_mode ?? 4;

  return (
    <section className="panel">
      <h2>Look</h2>
      <div className="modes">
        {MODES.map((m) => (
          <button
            key={m.value}
            type="button"
            disabled={disabled}
            className={`mode${params.mode === m.value ? " active" : ""}`}
            onClick={() => onChange({ mode: m.value })}
          >
            <strong>{m.label}</strong>
            <span>{m.blurb}</span>
          </button>
        ))}
      </div>

      {analysis && params.mode === "standard" && (
        <>
          <h2>
            How many colours
            <Info label="how many colours">
              <p>
                One colour is the paper, every other one is a filament you load
                by hand: the printer stops at a set height and waits for you to
                swap the spool. Two colours means one pause, four means three.
              </p>
              <p>
                The suggestion comes from how much of the artwork is halftone —
                hatching and screentone rather than flat black or white. Line
                art has little of it and prints well in two; heavily shaded art
                needs three or four, or that shading collapses into blocks.
              </p>
            </Info>
          </h2>
          <div className="toggles">
            {[2, 3, 4].map((n) => (
              <button
                key={n}
                type="button"
                disabled={disabled}
                className={`toggle${(params.color_mode ?? analysis.color_mode) === n ? " active" : ""}`}
                onClick={() => onChange({ color_mode: n })}
              >
                {n}
                {n === analysis.color_mode && <em>suggested</em>}
              </button>
            ))}
          </div>
          <p className="hint">
            {colours - 1} filament {colours - 1 === 1 ? "change" : "changes"} while
            printing. The analysis found {analysis.halftone_pct.toFixed(1)}%
            halftones, which suits {analysis.color_mode}.
          </p>
          {colours === 2 && ambiguous !== null && ambiguous >= AMBIGUOUS_WARN && (
            <p className="hint warn">
              {/* Not "halftones": that number counts anti-aliasing on every
                  edge and flags clean line art too. This is the area that
                  actually sits near the cut — hatching and screentone — and
                  it moves with the slider. */}
              About {Math.round(ambiguous * 100)}% of this artwork is shading
              close to the current cut: it will print all ink or all paper, and
              flip with a small move of the slider. With 3 or 4 colours it
              becomes a tone instead.
            </p>
          )}
        </>
      )}
    </section>
  );
}
