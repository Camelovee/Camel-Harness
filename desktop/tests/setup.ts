import { afterEach, beforeEach, vi } from "vitest";

beforeEach(() => {
  vi.stubGlobal("camelDesktop", {
    selectDirectory: vi.fn(async () => null),
    appInfo: vi.fn(async () => ({ version: "test", platform: "test" })),
    onAction: vi.fn(() => vi.fn()),
  });
});

afterEach(() => vi.unstubAllGlobals());
