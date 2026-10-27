// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { chooseLocalWorkspace } from "../src/features/workspaces/api";

afterEach(() => { delete window.camelDesktop; vi.unstubAllGlobals(); });

it("桌面目录选择走原生桥接，取消保持 null，浏览器走原有接口", async () => {
  const selectDirectory = vi.fn().mockResolvedValueOnce("/中文 项目").mockResolvedValueOnce(null);
  window.camelDesktop = { selectDirectory, appInfo: vi.fn(), onAction: vi.fn() };
  const request = vi.fn(async () => new Response('{"workspace":"/browser"}'));
  vi.stubGlobal("fetch", request);
  expect(await chooseLocalWorkspace()).toBe("/中文 项目");
  expect(await chooseLocalWorkspace()).toBeNull();
  expect(request).not.toHaveBeenCalled();
  delete window.camelDesktop;
  expect(await chooseLocalWorkspace()).toBe("/browser");
  expect(request).toHaveBeenCalledWith("/api/workspaces/select", expect.objectContaining({ method: "POST" }));
});
