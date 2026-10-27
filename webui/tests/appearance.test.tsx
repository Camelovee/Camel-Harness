// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useAppearance } from "../src/features/settings/useAppearance";
import { useAppShortcuts } from "../src/app/useAppShortcuts";
import { ToolDetailsPanel } from "../src/features/agent/ToolDetailsPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete window.camelDesktop; });

it("跟随系统响应主题变化，显式主题覆盖系统，卸载时移除监听", () => {
  const preference = new EventTarget() as EventTarget & { matches: boolean };
  preference.matches = true;
  vi.stubGlobal("matchMedia", vi.fn(() => preference));
  const { rerender, unmount } = renderHook(({ theme }) => useAppearance(theme), {
    initialProps: { theme: "system" as "system" | "light" | "dark" },
  });
  expect(document.documentElement.dataset.theme).toBe("dark");
  preference.matches = false;
  act(() => preference.dispatchEvent(new Event("change")));
  expect(document.documentElement.dataset.theme).toBe("light");
  rerender({ theme: "dark" });
  expect(document.documentElement.dataset.theme).toBe("dark");
  unmount();
  act(() => preference.dispatchEvent(new Event("change")));
  expect(document.documentElement.dataset.theme).toBe("dark");
});

it("浏览器快捷键使用最新操作，输入法组合时不触发", () => {
  const newChat = vi.fn();
  const openProject = vi.fn();
  const settings = vi.fn();
  const toggleSidebar = vi.fn();
  renderHook(() => useAppShortcuts({ "new-chat": newChat, "open-project": openProject, settings, "toggle-sidebar": toggleSidebar }));
  fireEvent.keyDown(window, { key: "n", ctrlKey: true });
  fireEvent.keyDown(window, { key: "o", metaKey: true });
  fireEvent.keyDown(window, { key: "b", ctrlKey: true });
  fireEvent.keyDown(window, { key: ",", metaKey: true });
  fireEvent.keyDown(window, { key: "n", ctrlKey: true, isComposing: true });
  expect([newChat, openProject, settings, toggleSidebar].map((callback) => callback.mock.calls.length)).toEqual([1, 1, 1, 1]);
});

it("桌面菜单事件只注册一次，卸载取消订阅", () => {
  const unsubscribe = vi.fn();
  const onAction = vi.fn(() => unsubscribe);
  window.camelDesktop = { selectDirectory: vi.fn(), appInfo: vi.fn(), onAction };
  const { rerender, unmount } = renderHook(() => useAppShortcuts({ "new-chat": vi.fn(), "open-project": vi.fn(), settings: vi.fn(), "toggle-sidebar": vi.fn() }));
  rerender();
  expect(onAction).toHaveBeenCalledTimes(1);
  unmount();
  expect(unsubscribe).toHaveBeenCalledTimes(1);
});

it("工具详情聚焦关闭按钮并可使用 Escape 收起", () => {
  const close = vi.fn();
  render(<ToolDetailsPanel message={{ id: "tool", role: "toolResult", content: [{ type: "tool_result", name: "read_file", content: "中文结果" }] }} onClose={close} />);
  expect(screen.getByText("中文结果")).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "关闭工具详情" }));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
});
