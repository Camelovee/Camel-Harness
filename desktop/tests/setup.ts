import { afterEach, beforeEach, vi } from "vitest";
import type { DesktopBridge } from "../bridge";

beforeEach(() => {
  vi.stubGlobal("camelDesktop", {
    selectDirectory: vi.fn(async () => null),
    appInfo: vi.fn(async () => ({ version: "test", platform: "test" })),
    retryBackend: vi.fn(async () => {}),
    onAction: vi.fn(() => vi.fn()),
  } satisfies DesktopBridge);
});

afterEach(() => vi.unstubAllGlobals());
