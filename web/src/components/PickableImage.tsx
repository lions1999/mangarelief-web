/**
 * The source image, zoomable, with a click that samples the local tone.
 *
 * Two things it must get right, both learned the hard way:
 *
 * 1. **It averages a small disc, it does not read a pixel.** On hatched
 *    artwork a single pixel is a coin toss — sampling the same shaded cheek
 *    600 times returns anything from 9 to 255 (sd 61), because you either hit
 *    a line or the paper between lines. The tone of a shaded area *is* its ink
 *    coverage, so the mean over a small disc is the quantity that means
 *    something. A median is not: on dense hatching it returns the paper and
 *    calls a 25%-ink region white.
 *
 * 2. **The sampled value comes from the original image**, never from the
 *    preview — sampling a quantised preview feeds its own output back in.
 *
 * The disc is fixed in source pixels, so zooming changes what you can aim at
 * but never what a click measures.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { RGB } from "../types";

interface Props {
  file: File;
  disabled?: boolean;
  onPick: (colour: RGB) => void;
}

/** Sampling buffer resolution. Big enough to zoom into, bounded for memory. */
const BUFFER_EDGE = 1600;
/**
 * Radius of the sampled disc, as a fraction of the image's long edge, so the
 * same click measures the same relative area whatever resolution was uploaded.
 *
 * Measured on hatched art: at this size a reading of a sparsely hatched region
 * lands within ±7 of its true mean, against ±18 for a disc half as wide, which
 * is too small to reliably contain a hatch line. Twice as wide is steadier
 * still but starts bleeding across edges — a disc 3px inside a black fill
 * already reads 25 instead of 12.
 */
const SAMPLE_FRACTION = 1 / 200;
const MIN_SAMPLE_R = 3;
const MIN_ZOOM = 1;
const MAX_ZOOM = 12;
/** A press that moves less than this is a click, not a pan. */
const DRAG_SLOP = 4;

/** Visible region of the source, in buffer pixels. */
interface View { x: number; y: number; w: number; h: number }

export default function PickableImage({ file, disabled, onPick }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const buffer = useRef<HTMLCanvasElement | null>(null);
  const view = useRef<View | null>(null);
  const radius = useRef(MIN_SAMPLE_R);
  const drag = useRef<{ x: number; y: number; moved: number } | null>(null);
  const hover = useRef<{ x: number; y: number } | null>(null);
  const [zoomed, setZoomed] = useState(false);

  const draw = useCallback(() => {
    const c = canvas.current, b = buffer.current, v = view.current;
    if (!c || !b || !v) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.imageSmoothingEnabled = v.w >= c.width;   // crisp pixels once zoomed in
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.drawImage(b, v.x, v.y, v.w, v.h, 0, 0, c.width, c.height);

    // The sampling disc, drawn where the cursor is: what a click will average
    // should be visible, not guessed.
    const h = hover.current;
    if (h && !disabled) {
      const r = Math.max(3, (radius.current * c.width) / v.w);
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(0,0,0,0.85)";
      ctx.beginPath();
      ctx.arc(h.x, h.y, r, 0, Math.PI * 2);
      ctx.stroke();
      ctx.lineWidth = 1;
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.stroke();
    }
  }, [disabled]);

  useEffect(() => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, BUFFER_EDGE / Math.max(img.width, img.height));
      const bw = Math.round(img.width * scale);
      const bh = Math.round(img.height * scale);

      const b = document.createElement("canvas");
      b.width = bw;
      b.height = bh;
      b.getContext("2d")?.drawImage(img, 0, 0, bw, bh);
      buffer.current = b;
      view.current = { x: 0, y: 0, w: bw, h: bh };
      radius.current = Math.max(MIN_SAMPLE_R,
        Math.round(Math.max(bw, bh) * SAMPLE_FRACTION));

      const c = canvas.current;
      if (c) {
        // The visible canvas keeps the image's aspect but its own resolution,
        // so a zoomed view is rendered from the buffer rather than magnified.
        const vs = Math.min(1, 1024 / Math.max(bw, bh));
        c.width = Math.round(bw * vs);
        c.height = Math.round(bh * vs);
      }
      setZoomed(false);
      draw();
      URL.revokeObjectURL(url);
    };
    img.src = url;
    return () => URL.revokeObjectURL(url);
  }, [file, draw]);

  /** Canvas coordinates from a pointer event. */
  const at = (e: React.PointerEvent | React.WheelEvent) => {
    const c = canvas.current!;
    const rect = c.getBoundingClientRect();
    return {
      x: ((e.clientX - rect.left) / rect.width) * c.width,
      y: ((e.clientY - rect.top) / rect.height) * c.height,
    };
  };

  const clamp = (v: View) => {
    const b = buffer.current!;
    v.w = Math.min(v.w, b.width);
    v.h = Math.min(v.h, b.height);
    v.x = Math.max(0, Math.min(b.width - v.w, v.x));
    v.y = Math.max(0, Math.min(b.height - v.h, v.y));
    return v;
  };

  const wheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    const b = buffer.current, v = view.current, c = canvas.current;
    if (!b || !v || !c) return;
    e.preventDefault();
    const p = at(e);
    // Keep the point under the cursor fixed: zooming towards a detail is the
    // whole reason for zooming.
    const sx = v.x + (p.x / c.width) * v.w;
    const sy = v.y + (p.y / c.height) * v.h;
    const current = b.width / v.w;
    const next = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM,
      current * (e.deltaY < 0 ? 1.25 : 0.8)));
    v.w = b.width / next;
    v.h = b.height / next;
    v.x = sx - (p.x / c.width) * v.w;
    v.y = sy - (p.y / c.height) * v.h;
    clamp(v);
    setZoomed(next > 1.001);
    hover.current = p;
    draw();
  };

  const down = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const p = at(e);
    drag.current = { x: p.x, y: p.y, moved: 0 };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const move = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const c = canvas.current, v = view.current;
    if (!c || !v) return;
    const p = at(e);
    hover.current = p;
    const d = drag.current;
    if (d) {
      const dx = p.x - d.x, dy = p.y - d.y;
      d.moved += Math.abs(dx) + Math.abs(dy);
      v.x -= (dx / c.width) * v.w;
      v.y -= (dy / c.height) * v.h;
      clamp(v);
      d.x = p.x;
      d.y = p.y;
    }
    draw();
  };

  const leave = () => {
    hover.current = null;
    draw();
  };

  const up = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    drag.current = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
    if (disabled || !d || d.moved > DRAG_SLOP) return;   // that was a pan

    const b = buffer.current, v = view.current, c = canvas.current;
    if (!b || !v || !c) return;
    const ctx = b.getContext("2d", { willReadFrequently: true });
    if (!ctx) return;
    const p = at(e);
    const sx = Math.round(v.x + (p.x / c.width) * v.w);
    const sy = Math.round(v.y + (p.y / c.height) * v.h);

    const rad = radius.current;
    const x0 = Math.max(0, sx - rad);
    const y0 = Math.max(0, sy - rad);
    const x1 = Math.min(b.width, sx + rad + 1);
    const y1 = Math.min(b.height, sy + rad + 1);
    const { data } = ctx.getImageData(x0, y0, x1 - x0, y1 - y0);

    let r = 0, g = 0, bl = 0, n = 0;
    for (let y = y0; y < y1; y++) {
      for (let x = x0; x < x1; x++) {
        const dx = x - sx, dy = y - sy;
        if (dx * dx + dy * dy > rad * rad) continue;   // disc, not square
        const i = ((y - y0) * (x1 - x0) + (x - x0)) * 4;
        r += data[i]; g += data[i + 1]; bl += data[i + 2]; n++;
      }
    }
    if (!n) return;
    onPick([Math.round(r / n), Math.round(g / n), Math.round(bl / n)]);
  };

  const reset = () => {
    const b = buffer.current;
    if (!b) return;
    view.current = { x: 0, y: 0, w: b.width, h: b.height };
    setZoomed(false);
    draw();
  };

  return (
    <div className="picker">
      <canvas
        ref={canvas}
        className={disabled ? "" : "pickable"}
        onWheel={wheel}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerLeave={leave}
      />
      {zoomed && (
        <button type="button" className="picker-reset" onClick={reset}>
          fit
        </button>
      )}
    </div>
  );
}
