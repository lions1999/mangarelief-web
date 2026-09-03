/**
 * The source image on a canvas, so a click can read the real pixel colour.
 *
 * Both modes need this: Spot picks accents, Standard picks tone landmarks.
 * The picked value always comes from the original image, never from the
 * preview — sampling a quantised preview would feed its own output back in.
 */
import { useEffect, useRef } from "react";
import type { RGB } from "../types";

interface Props {
  file: File;
  disabled?: boolean;
  onPick: (colour: RGB) => void;
}

const MAX_EDGE = 520;

export default function PickableImage({ file, disabled, onPick }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const c = canvas.current;
      if (!c) return;
      const scale = Math.min(1, MAX_EDGE / Math.max(img.width, img.height));
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext("2d")?.drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
    };
    img.src = url;
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const pick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (disabled) return;
    const c = canvas.current;
    const ctx = c?.getContext("2d", { willReadFrequently: true });
    if (!c || !ctx) return;
    const rect = c.getBoundingClientRect();
    const x = Math.floor(((e.clientX - rect.left) / rect.width) * c.width);
    const y = Math.floor(((e.clientY - rect.top) / rect.height) * c.height);
    const [r, g, b] = ctx.getImageData(x, y, 1, 1).data;
    onPick([r, g, b]);
  };

  return <canvas ref={canvas} onClick={pick} className={disabled ? "" : "pickable"} />;
}
