import { useEffect, useRef } from "react";

const SCRIPT_ID = "cloudflare-turnstile-script";

export default function TurnstileChallenge({ onToken, onError }) {
  const containerRef = useRef(null);
  const widgetRef = useRef(null);
  const siteKey = import.meta.env.VITE_TURNSTILE_SITE_KEY;

  useEffect(() => {
    if (!siteKey) return undefined;

    const render = () => {
      if (!containerRef.current || !window.turnstile || widgetRef.current !== null) return;
      widgetRef.current = window.turnstile.render(containerRef.current, {
        sitekey: siteKey,
        theme: "dark",
        callback: (token) => {
          onToken(token);
          onError?.("");
        },
        "expired-callback": () => {
          onToken(null);
          onError?.("Security verification expired. Please complete it again.");
        },
        "error-callback": () => {
          onToken(null);
          onError?.("Security verification could not be completed. Please try again.");
        },
      });
    };

    let script = document.getElementById(SCRIPT_ID);
    if (!script) {
      script = document.createElement("script");
      script.id = SCRIPT_ID;
      script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      script.async = true;
      script.defer = true;
      document.head.appendChild(script);
    }
    script.addEventListener("load", render);
    render();

    return () => {
      script?.removeEventListener("load", render);
      if (window.turnstile && widgetRef.current !== null) window.turnstile.remove(widgetRef.current);
      widgetRef.current = null;
    };
  }, [onError, onToken, siteKey]);

  if (!siteKey) {
    return <p className="text-xs text-amber-400">CAPTCHA is required. Configure VITE_TURNSTILE_SITE_KEY.</p>;
  }
  return <div ref={containerRef} aria-label="Security verification" />;
}
