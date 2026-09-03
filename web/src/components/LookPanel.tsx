/**
 * What kind of relief, and how many filaments it costs to print.
 *
 * These two belong together and above everything else: the mode decides which
 * colour controls exist at all, and the count is the first thing a visitor
 * weighs, because each extra colour is one more bobbin to own and one more
 * pause to stand through.
 */
import type { Analysis, JobParams, Mode } from "../types";

interface Props {
  params: JobParams;
  analysis: Analysis | null;
  disabled: boolean;
  onChange: (patch: Partial<JobParams>) => void;
}

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

export default function LookPanel({ params, analysis, disabled, onChange }: Props) {
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
          <h2>How many colours</h2>
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
        </>
      )}
    </section>
  );
}
