/**
 * Accent picking + coverage, with the live 2D mockup beside them.
 *
 * Coverage is impossible to judge from a number: it decides how far an accent
 * spreads into muted tones, and the only way to set it is to see the result.
 * The preview call is debounced because it costs a server round-trip today —
 * and is the one call phase 4 replaces with a local port of the classifier.
 */
import { useEffect, useRef, useState } from "react";
import { mockup } from "../api";
import type { JobParams, RGB } from "../types";

interface Props {
  file: File;
  params: JobParams;
  suggested: RGB[];
  disabled: boolean;
  onChange: (patch: Partial<JobParams>) => void;
}

const DEBOUNCE_MS = 350;

const hex = ([r, g, b]: RGB) =>
  "#" + [r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("");

export default function SpotPanel({ file, params, suggested, disabled, onChange }: Props) {
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [slot, setSlot] = useState(0);
  const canvas = useRef<HTMLCanvasElement>(null);

  // Source image on a canvas, so a click can read the real pixel colour.
  useEffect(() => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const c = canvas.current;
      if (!c) return;
      const scale = Math.min(1, 520 / Math.max(img.width, img.height));
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext("2d")?.drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
    };
    img.src = url;
    return () => URL.revokeObjectURL(url);
  }, [file]);

  // Debounced mockup refresh.
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setBusy(true);
      try {
        const url = await mockup(
          file,
          {
            mode: "spot_color",
            spot_accents: params.spot_accents,
            spot_coverage: params.spot_coverage,
            autodetect_accents: params.spot_accents.length === 0,
          },
          controller.signal,
        );
        setPreview((old) => {
          if (old) URL.revokeObjectURL(old);
          return url;
        });
        setProblem("");
      } catch (err) {
        if (!controller.signal.aborted) {
          setProblem(err instanceof Error ? err.message : "preview failed");
        }
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    }, DEBOUNCE_MS);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [file, params.spot_accents, params.spot_coverage]);

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  const accents = params.spot_accents;
  const setAccent = (index: number, colour: RGB | null) => {
    const next = [...accents];
    if (colour === null) next.splice(index, 1);
    else if (index < next.length) next[index] = colour;
    else next.push(colour);
    onChange({ spot_accents: next.slice(0, 2), autodetect_accents: next.length === 0 });
  };

  const pick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (disabled) return;
    const c = canvas.current;
    const ctx = c?.getContext("2d", { willReadFrequently: true });
    if (!c || !ctx) return;
    const rect = c.getBoundingClientRect();
    const x = Math.floor(((e.clientX - rect.left) / rect.width) * c.width);
    const y = Math.floor(((e.clientY - rect.top) / rect.height) * c.height);
    const [r, g, b] = ctx.getImageData(x, y, 1, 1).data;
    setAccent(slot, [r, g, b]);
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
          <canvas ref={canvas} onClick={pick} className={disabled ? "" : "pickable"} />
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
