// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useAppearance } from "../../renderer/src/features/settings/useAppearance";
import { useAppShortcuts } from "../../renderer/src/app/useAppShortcuts";
import { ToolDetailsPanel } from "../../renderer/src/features/agent/ToolDetailsPanel";
import type { DesktopAction } from "../../renderer/src/shared/platform";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

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

it("原生菜单的四种操作会分发到当前回调，重新渲染后使用最新状态", () => {
  const newChat = vi.fn();
  const openProject = vi.fn();
  const settings = vi.fn();
  const toggleSidebar = vi.fn();
  let dispatch!: (action: DesktopAction) => void;
  window.camelDesktop.onAction = (callback) => { dispatch = callback; return vi.fn(); };
  const { rerender } = renderHook(({ onNewChat }) => useAppShortcuts({ "new-chat": onNewChat, "open-project": openProject, settings, "toggle-sidebar": toggleSidebar }), { initialProps: { onNewChat: newChat } });
  act(() => { dispatch("new-chat"); dispatch("open-project"); dispatch("toggle-sidebar"); dispatch("settings"); });
  expect([newChat, openProject, settings, toggleSidebar].map((callback) => callback.mock.calls.length)).toEqual([1, 1, 1, 1]);
  const updatedNewChat = vi.fn();
  rerender({ onNewChat: updatedNewChat });
  act(() => dispatch("new-chat"));
  expect(newChat).toHaveBeenCalledOnce();
  expect(updatedNewChat).toHaveBeenCalledOnce();
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
