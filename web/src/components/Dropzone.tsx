import { useRef, useState } from "react";

interface Props {
  onFile: (file: File) => void;
  disabled?: boolean;
}

const MAX_MB = 12;

export default function Dropzone({ onFile, disabled }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [problem, setProblem] = useState("");

  const accept = (file?: File | null) => {
    if (!file) return;
    // Mirrors the server's limit so an oversize file fails instantly instead of
    // after a full upload.
    if (file.size > MAX_MB * 1024 * 1024) {
      setProblem(`${file.name} is ${(file.size / 1024 / 1024).toFixed(1)} MB — the limit is ${MAX_MB} MB`);
      return;
    }
    setProblem("");
    onFile(file);
  };

  return (
    <div>
      <div
        className={`dropzone${over ? " over" : ""}${disabled ? " disabled" : ""}`}
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
        <strong>Drop your artwork here</strong>
        <span>or click to choose — PNG, JPG, WebP, HEIC · up to {MAX_MB} MB</span>
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
