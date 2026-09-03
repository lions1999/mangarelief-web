/**
 * The greys the relief is calibrated from, as sidebar controls.
 *
 * The desktop app has always let you sample these off the artwork, because
 * "auto" gets the midtones wrong on any panel with a large flat grey — and a
 * wrong midtone puts a whole face on the wrong filament. The sampling itself
 * happens on the stage; this panel picks which slot a click fills and says
 * what it did.
 *
 * Only the slots the current colour count actually reads are shown. The engine
 * ignores the rest, and a swatch that does nothing is worse than no swatch.
 */
import { TONE_LABELS, type Tones } from "../hooks/useTones";

interface Props {
  tones: Tones;
  disabled: boolean;
  /** Two colours: the coverage cut, 0..1, and the ink level the analysis found. */
  coverage: number;
  inkLevel: number | null;
  onCoverage: (value: number) => void;
}

const greyHex = (v: number) => "#" + v.toString(16).padStart(2, "0").repeat(3);

export default function TonesPanel({ tones, disabled, coverage, inkLevel, onCoverage }: Props) {
  const { values, active, colours, slot, manual, picked } = tones;
  if (!values) return null;

  // Two colours is a different control entirely. There is no tone to sample:
  // the image decides what counts as ink, and the one question left is how
  // loaded a shaded area must be before it prints black.
  if (colours === 2) {
    const pct = Math.round(coverage * 100);
    return (
      <section className="panel">
        <h2>Ink coverage</h2>
        <label className="field">
          <span>
            Shading darker than <em>{pct}%</em>
          </span>
          <input
            type="range"
            min={10}
            max={90}
            step={5}
            value={pct}
            disabled={disabled}
            onChange={(e) => onCoverage(Number(e.target.value) / 100)}
          />
        </label>
        <p className="hint">
          A zone prints as ink when at least {pct}% of its area is inked, judged
          over about 0.7 mm — what the nozzle can resolve. Lower keeps more
          hatching black; higher keeps more of it paper. Fine linework survives
          either way.
          {inkLevel !== null && (
            <> Ink here is anything darker than {inkLevel}, read from the image's histogram.</>
          )}
        </p>
      </section>
    );
  }


  return (
    <section className="panel">
      <h2>Tones</h2>

      <div className="accents">
        {active.map((i) => (
          <button
            key={i}
            type="button"
            disabled={disabled}
            className={`swatch${slot === i ? " active" : ""}`}
            style={{ background: greyHex(values[i]) }}
            onClick={() => tones.select(i)}
            title={`${TONE_LABELS[i]} — ${values[i]}`}
          />
        ))}
        {manual && (
          <button type="button" className="link" disabled={disabled} onClick={tones.reset}>
            back to auto
          </button>
        )}
      </div>

      <p className="hint">
        {TONE_LABELS[slot]} · {values[slot]}
        {manual ? "" : " · detected automatically"} — click the artwork to sample
        into this slot. A click averages the small disc under the cursor: on
        hatched art a single pixel is either the line or the paper between
        lines, never the tone of the area.
        {picked && picked.value !== picked.clamped && (
          <>
            {" "}Sampled {picked.value}, kept at {picked.clamped} so the tones
            stay in order.
          </>
        )}
      </p>

    </section>
  );
}
