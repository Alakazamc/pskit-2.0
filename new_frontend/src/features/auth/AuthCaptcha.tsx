import { useEffect, useRef } from "react";

const SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

export type AuthCaptchaAction =
  | "signup"
  | "recovery"
  | "guest_upgrade_email"
  | "guest_anonymous";

type TurnstileApi = {
  render(container: HTMLElement, options: {
    sitekey: string;
    action: AuthCaptchaAction;
    theme: "auto";
    size: "flexible";
    callback: (token: string) => void;
    "expired-callback": () => void;
    "error-callback": () => void;
  }): string;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
};

declare global {
  interface Window { turnstile?: TurnstileApi }
}

export function AuthCaptcha({ siteKey, action, resetSignal, onToken, onUnavailable }: {
  siteKey: string;
  action: AuthCaptchaAction;
  resetSignal: number;
  onToken: (token: string | null) => void;
  onUnavailable?: () => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const widgetId = useRef<string | null>(null);
  const onTokenRef = useRef(onToken);
  const onUnavailableRef = useRef(onUnavailable);
  onTokenRef.current = onToken;
  onUnavailableRef.current = onUnavailable;

  useEffect(() => {
    let mounted = true;
    onTokenRef.current(null);
    const renderWidget = () => {
      if (!mounted || !container.current || !window.turnstile || widgetId.current) return;
      widgetId.current = window.turnstile.render(container.current, {
        sitekey: siteKey,
        action,
        theme: "auto",
        size: "flexible",
        callback: (token) => onTokenRef.current(token),
        "expired-callback": () => onTokenRef.current(null),
        "error-callback": () => {
          onTokenRef.current(null);
          onUnavailableRef.current?.();
        },
      });
    };
    const scriptError = () => onUnavailableRef.current?.();
    let script = document.querySelector<HTMLScriptElement>(`script[src="${SCRIPT_URL}"]`);
    if (window.turnstile) renderWidget();
    else {
      if (!script) {
        script = document.createElement("script");
        script.src = SCRIPT_URL;
        script.async = true;
        document.head.appendChild(script);
      }
      script.addEventListener("load", renderWidget);
      script.addEventListener("error", scriptError);
    }
    return () => {
      mounted = false;
      script?.removeEventListener("load", renderWidget);
      script?.removeEventListener("error", scriptError);
      if (widgetId.current && window.turnstile) window.turnstile.remove(widgetId.current);
      widgetId.current = null;
      onTokenRef.current(null);
    };
  }, [action, siteKey]);

  useEffect(() => {
    if (resetSignal > 0 && widgetId.current && window.turnstile) {
      onTokenRef.current(null);
      window.turnstile.reset(widgetId.current);
    }
  }, [resetSignal]);

  return <div className="auth-captcha" ref={container} />;
}
