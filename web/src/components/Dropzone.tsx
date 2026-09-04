import { useRef, useState } from "react";

interface Props {
  onFile: (file: File) => void;
  disabled?: boolean;
  /**
   * Il tetto del server, in MB. Nullo finche' non si sa: in quel caso non si
   * controlla e non si annuncia niente — sbagliare il numero qui vuol dire
   * rifiutare un file che il server avrebbe accettato.
   */
  maxMb?: number | null;
  /** The small variant that sits in the sidebar once artwork is loaded. */
  compact?: boolean;
}

export default function Dropzone({ onFile, disabled, maxMb, compact }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [problem, setProblem] = useState("");

  const accept = (file?: File | null) => {
    if (!file) return;
    // Mirrors the server's limit so an oversize file fails instantly instead of
    // after a full upload.
    if (maxMb != null && file.size > maxMb * 1024 * 1024) {
      setProblem(`${file.name} is ${(file.size / 1024 / 1024).toFixed(1)} MB — the limit is ${maxMb} MB`);
      return;
    }
    setProblem("");
    onFile(file);
  };

  return (
    <div>
      <div
        className={`dropzone${compact ? " compact" : ""}${over ? " over" : ""}${disabled ? " disabled" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (!disabled) accept(e.dataTransfer.files?.[0]);
        }}
        onClick={() => !disabled && input.current?.click()}
      >
        <strong>{compact ? "Replace the artwork" : "Drop your artwork here"}</strong>
        <span>
          {compact
            ? "drop or click"
            : `or click to choose — PNG, JPG, WebP, HEIC${maxMb != null ? ` · up to ${maxMb} MB` : ""}`}
        </span>
        <input
          ref={input}
          type="file"
          accept="image/*"
          hidden
          onChange={(e) => accept(e.target.files?.[0])}
        />
      </div>
      {problem && <p className="field-error">{problem}</p>}
    </div>
  );
}
