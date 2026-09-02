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

export default function ParamsPanel({ params, analysis, disabled, onChange }: Props) {
  const layers = Math.round((params.max_h - params.base_h) / params.layer_height);

  return (
    <section className="panel">
      <h2>2 · Choose a look</h2>
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
        <p className="hint">
          {analysis.halftone_pct.toFixed(1)}% halftones detected →{" "}
          <strong>{analysis.color_mode}-colour mode</strong>, filament changes at{" "}
          {analysis.suggested_color_changes_z.filter((z) => z > 0).join(" / ")} mm.
        </p>
      )}

      <h2>3 · Size and print</h2>
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

      <p className="hint">
        {layers} printed layers above the base. Free generations run at draft
        resolution (800 px) — enough to print, not the finest detail the engine
        can do.
      </p>
    </section>
  );
}
