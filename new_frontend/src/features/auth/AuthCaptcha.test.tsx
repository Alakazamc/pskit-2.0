import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthCaptcha } from "./AuthCaptcha";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("binds a Turnstile widget to the server-defined action and resets tokens", () => {
  const onToken = vi.fn();
  const reset = vi.fn();
  const remove = vi.fn();
  let options: Record<string, unknown> = {};
  const renderWidget = vi.fn((_node, supplied) => { options = supplied; return "widget-1"; });
  vi.stubGlobal("turnstile", { render: renderWidget, reset, remove });

  const view = render(<AuthCaptcha siteKey="site" action="signup" resetSignal={0}
    onToken={onToken} />);
  expect(options).toMatchObject({
    sitekey: "site",
    action: "signup",
    theme: "auto",
    size: "flexible",
  });
  (options.callback as (token: string) => void)("proof");
  expect(onToken).toHaveBeenCalledWith("proof");
  (options["expired-callback"] as () => void)();
  expect(onToken).toHaveBeenCalledWith(null);

  view.rerender(<AuthCaptcha siteKey="site" action="signup" resetSignal={1}
    onToken={onToken} />);
  expect(reset).toHaveBeenCalledWith("widget-1");
});

it("recreates the widget when its action changes", () => {
  const renderWidget = vi.fn().mockReturnValueOnce("one").mockReturnValueOnce("two");
  const remove = vi.fn();
  vi.stubGlobal("turnstile", { render: renderWidget, reset: vi.fn(), remove });
  const view = render(<AuthCaptcha siteKey="site" action="signup" resetSignal={0}
    onToken={() => {}} />);

  view.rerender(<AuthCaptcha siteKey="site" action="recovery" resetSignal={0}
    onToken={() => {}} />);

  expect(remove).toHaveBeenCalledWith("one");
  expect(renderWidget).toHaveBeenLastCalledWith(expect.any(HTMLElement),
    expect.objectContaining({ action: "recovery" }));
});
