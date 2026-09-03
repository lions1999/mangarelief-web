/**
 * The four greys the relief is built from, as sidebar controls.
 *
 * The desktop app has always let you sample these off the artwork, because
 * "auto" gets the midtones wrong on any panel with a large flat grey — and a
 * wrong midtone puts a whole face on the wrong filament. The sampling itself
 * happens on the stage; this panel picks which slot a click fills and says
 * what it did.
 */
import { TONE_LABELS, type Tones } from "../hooks/useTones";

interface Props {
  tones: Tones;
  disabled: boolean;
}

const greyHex = (v: number) => "#" + v.toString(16).padStart(2, "0").repeat(3);

export default function TonesPanel({ tones, disabled }: Props) {
  const { values, slot, manual, picked } = tones;
  if (!values) return null;

  return (
    <section className="panel">
      <h2>Tones</h2>

      <div className="accents">
        {values.map((v, i) => (
          <button
            key={i}
            type="button"
            disabled={disabled}
            className={`swatch${slot === i ? " active" : ""}`}
            style={{ background: greyHex(v) }}
            onClick={() => tones.select(i)}
            title={`${TONE_LABELS[i]} — ${v}`}
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
        into this slot.
        {picked && picked.value !== picked.clamped && (
          <>
            {" "}Sampled {picked.value}, kept at {picked.clamped} so the four
            tones stay in order.
          </>
        )}
      </p>
    </section>
  );
}
