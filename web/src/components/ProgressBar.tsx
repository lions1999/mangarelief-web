interface Props {
  progress: number;
  message: string;
  /** Shown while the request is in flight but the server has not answered yet. */
  cold?: boolean;
}

export default function ProgressBar({ progress, message, cold }: Props) {
  return (
    <div className="progress">
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${Math.max(progress, 3)}%` }} />
      </div>
      <p className="progress-label">
        {message || "Working…"} <span>{progress}%</span>
      </p>
      {cold && (
        <p className="hint">
          The server sleeps when nobody is using it, so the first run of the day
          takes a few extra seconds to wake up.
        </p>
      )}
    </div>
  );
}
