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
}

const greyHex = (v: number) => "#" + v.toString(16).padStart(2, "0").repeat(3);

export default function TonesPanel({ tones, disabled }: Props) {
  const { values, active, colours, slot, manual, picked } = tones;
  if (!values) return null;

  // At two colours there is one number that matters and it is not on a
  // swatch: everything darker than the midpoint of Paper and Ink becomes ink.
  const threshold = Math.round((values[0] + values[3]) / 2);

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

      {colours === 2 && (
        <p className="hint">
          Everything darker than <strong>{threshold}</strong> prints as ink —
          that is the midpoint of these two. Dense hatching averages into a grey
          once the image is scaled down, so a shaded face can fall on the ink
          side of it: sample the face into Paper to bring the line down.
        </p>
      )}
    </section>
  );
}
