/**
 * Physical size and layer settings — the same ranges the desktop spinboxes
 * enforce. Last in the sidebar: they are the settings you touch once, after
 * the colours are right.
 */
import Info from "./Info";
import type { JobParams } from "../types";

interface Props {
  params: JobParams;
  disabled: boolean;
  /** Il tetto di risoluzione del piano gratuito, dal server. Assente = non lo diciamo. */
  maxRes: number | null;
  onChange: (patch: Partial<JobParams>) => void;
}

export default function PrintPanel({ params, disabled, maxRes, onChange }: Props) {
  const layers = Math.round((params.max_h - params.base_h) / params.layer_height);

  return (
    <section className="panel">
      <h2>
        Size and print
        <Info label="size and print">
          <p>
            <strong>Total height</strong> is the tallest point of the relief and
            <strong> base thickness</strong> the flat slab underneath it. What
            lies between the two is what gets terraced, so a thicker base on the
            same total height leaves less room for the levels.
          </p>
          <p>
            <strong>Layer height</strong> is the printer's own step. Every level
            snaps to a multiple of it, so a finer layer buys finer terraces and a
            longer print.
          </p>
          {maxRes !== null && (
            <p>
              Free generations run at draft resolution, {maxRes} px on the long
              side: enough to print, not the finest detail the engine can do.
            </p>
          )}
        </Info>
      </h2>
      <label className="field">
        <span>
          Long side <em>{params.max_dim} mm</em>
        </span>
        <input
          type="range"
          min={40}
          max={200}
          step={5}
          value={params.max_dim}
          disabled={disabled}
          onChange={(e) => onChange({ max_dim: Number(e.target.value) })}
        />
      </label>

      <label className="field">
        <span>
          Total height <em>{params.max_h.toFixed(1)} mm</em>
        </span>
        <input
          type="range"
          min={1.0}
          max={5.0}
          step={0.2}
          value={params.max_h}
          disabled={disabled}
          onChange={(e) => onChange({ max_h: Number(e.target.value) })}
        />
      </label>

      <label className="field">
        <span>
          Base thickness <em>{params.base_h.toFixed(1)} mm</em>
        </span>
        <input
          type="range"
          min={0.4}
          max={Math.max(0.6, params.max_h - params.layer_height)}
          step={0.2}
          value={params.base_h}
          disabled={disabled}
          onChange={(e) => onChange({ base_h: Number(e.target.value) })}
        />
      </label>

      <label className="field">
        <span>
          Layer height <em>{params.layer_height.toFixed(2)} mm</em>
        </span>
        <select
          value={params.layer_height}
          disabled={disabled}
          onChange={(e) => onChange({ layer_height: Number(e.target.value) })}
        >
          <option value={0.08}>0.08 — fine</option>
          <option value={0.12}>0.12</option>
          <option value={0.2}>0.20 — standard</option>
          <option value={0.28}>0.28 — draft</option>
        </select>
      </label>

      <p className="hint">{layers} printed layers above the base.</p>
    </section>
  );
}
