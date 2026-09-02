/**
 * Cloudflare Turnstile, only when a site key is configured.
 *
 * Without VITE_TURNSTILE_SITE_KEY the component renders nothing and reports an
 * empty token: the API accepts that as long as TURNSTILE_SECRET is unset on the
 * server, so local development and the first deploy work without any of it.
 */
import { useEffect, useRef } from "react";

declare global {
  interface Window {
    turnstile?: {
      render: (el: HTMLElement, opts: Record<string, unknown>) => string;
      remove: (id: string) => void;
    };
  }
}

const SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY as string | undefined;
const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

export const turnstileEnabled = Boolean(SITE_KEY);

export default function Turnstile({ onToken }: { onToken: (token: string) => void }) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!SITE_KEY || !host.current) return;
    let widgetId: string | undefined;
    let cancelled = false;

    const render = () => {
      if (cancelled || !host.current || !window.turnstile) return;
      widgetId = window.turnstile.render(host.current, {
        sitekey: SITE_KEY,
        callback: onToken,
        "expired-callback": () => onToken(""),
        "error-callback": () => onToken(""),
        theme: "dark",
      });
    };

    if (window.turnstile) {
      render();
    } else {
      const existing = document.querySelector<HTMLScriptElement>(`script[src="${SCRIPT}"]`);
      const script = existing ?? document.createElement("script");
      if (!existing) {
        script.src = SCRIPT;
        script.async = true;
        document.head.appendChild(script);
      }
      script.addEventListener("load", render);
    }

    return () => {
      cancelled = true;
      if (widgetId && window.turnstile) window.turnstile.remove(widgetId);
    };
  }, [onToken]);

  if (!SITE_KEY) return null;
  return <div ref={host} className="turnstile" />;
}
