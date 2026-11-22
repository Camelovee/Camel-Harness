// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { chooseLocalWorkspace } from "../../renderer/src/features/workspaces/api";

afterEach(() => vi.unstubAllGlobals());

it("目录选择与取消始终走原生桥接，不向后端请求目录对话框", async () => {
  const selectDirectory = vi.fn().mockResolvedValueOnce("/中文 项目").mockResolvedValueOnce(null);
  window.camelDesktop = { ...window.camelDesktop, selectDirectory, appInfo: vi.fn(), onAction: vi.fn() };
  const request = vi.fn();
  vi.stubGlobal("fetch", request);
  expect(await chooseLocalWorkspace()).toBe("/中文 项目");
  expect(await chooseLocalWorkspace()).toBeNull();
  expect(request).not.toHaveBeenCalled();
  expect(selectDirectory).toHaveBeenCalledTimes(2);
});
