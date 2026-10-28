import { afterEach, beforeEach, vi } from "vitest";
import type { DesktopBridge } from "../bridge";

beforeEach(() => {
  vi.stubGlobal("camelDesktop", {
    selectDirectory: vi.fn(async () => null),
    appInfo: vi.fn(async () => ({ version: "test", platform: "test" })),
    retryBackend: vi.fn(async () => {}),
    onAction: vi.fn(() => vi.fn()),
  } satisfies DesktopBridge);
  vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  })));
  if (typeof Element !== "undefined") {
    Element.prototype.animate = vi.fn(() => ({ cancel: vi.fn() })) as unknown as Element["animate"];
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  }
});

afterEach(() => vi.unstubAllGlobals());
