// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import DesktopApp from "../../renderer/src/app/DesktopApp";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const responses: Record<string, object> = {
      "/api/settings": { models: [{ id: "model", name: "测试模型", model: "fake", provider: "anthropic", configured: true, context_window_tokens: 128000 }], default_model_id: "model", theme: "light" },
      "/api/workspaces": { workspaces: [] }, "/api/workspaces/default": { workspace: "/中文 项目" }, "/api/approvals": { approvals: [] },
    };
    if (!(url in responses)) throw new Error(`测试中未配置的接口：${url}`);
    return new Response(JSON.stringify(responses[url]));
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("默认展示三栏，快速收起与展开详情不依赖动画结束", async () => {
  render(<DesktopApp />);
  await screen.findByText("今天，想构建些什么？");
  expect(screen.getByRole("complementary", { name: "工作区与会话" })).toBeTruthy();
  expect(screen.getByRole("main")).toBeTruthy();
  expect(screen.getByRole("complementary", { name: "执行详情", exact: true })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "收起执行详情", exact: true }));
  expect(screen.queryByRole("complementary", { name: "执行详情", exact: true })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "展开执行详情", exact: true }));
  expect(screen.getByRole("button", { name: "收起执行详情", exact: true }).getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByRole("complementary", { name: "执行详情", exact: true })).toBeTruthy();
  expect(vi.mocked(Element.prototype.animate).mock.results[0].value.cancel).toHaveBeenCalled();
});

it("减少动态效果时，布局操作即时完成且不创建位置动画", async () => {
  const matchMedia = window.matchMedia;
  vi.stubGlobal("matchMedia", vi.fn((query: string) => ({ ...matchMedia(query), matches: query === "(prefers-reduced-motion: reduce)" })));
  render(<DesktopApp />);
  await screen.findByText("今天，想构建些什么？");
  fireEvent.click(screen.getByRole("button", { name: "收起侧栏" }));
  expect(screen.queryByRole("complementary", { name: "工作区与会话" })).toBeNull();
  expect(Element.prototype.animate).not.toHaveBeenCalled();
});

it("欢迎建议只填写草稿并聚焦输入，不创建会话或调用模型", async () => {
  render(<DesktopApp />);
  fireEvent.click(await screen.findByRole("button", { name: /理解项目/ }));
  const input = screen.getByRole("textbox", { name: "向 Camel 发送消息" });
  expect((input as HTMLTextAreaElement).value).toBe("帮我理解这个项目的结构和主要运行流程");
  expect(document.activeElement).toBe(input);
  expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
});
