// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { usePanelSizes } from "../../renderer/src/app/usePanelSizes";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("两侧调整受到中央最小阅读宽度约束", () => {
  vi.stubGlobal("innerWidth", 1180);
  const { result } = renderHook(() => usePanelSizes(true, true));
  act(() => result.current.resizeSidebar(360));
  act(() => result.current.resizeInspector(440));
  expect(result.current.sidebarWidth).toBe(360);
  expect(result.current.inspectorWidth).toBe(384);
  expect(1180 - result.current.sidebarWidth - result.current.inspectorWidth - 16).toBe(420);
  act(() => result.current.resizeSidebar(1000));
  expect(result.current.sidebarWidth).toBe(360);
  act(() => result.current.resizeInspector(0));
  expect(result.current.inspectorWidth).toBe(240);
});

it("缩窗后重新放大恢复原有宽度，收起面板也保留选择", () => {
  vi.stubGlobal("innerWidth", 1600);
  const { result, rerender } = renderHook(({ sidebar, inspector }) => usePanelSizes(sidebar, inspector), {
    initialProps: { sidebar: true, inspector: true },
  });
  act(() => { result.current.resizeSidebar(340); result.current.resizeInspector(440); });
  act(() => { vi.stubGlobal("innerWidth", 1180); window.dispatchEvent(new Event("resize")); });
  expect(result.current.inspectorWidth).toBe(404);
  act(() => { vi.stubGlobal("innerWidth", 1600); window.dispatchEvent(new Event("resize")); });
  expect(result.current.inspectorWidth).toBe(440);
  rerender({ sidebar: false, inspector: false });
  rerender({ sidebar: true, inspector: true });
  expect(result.current.sidebarWidth).toBe(340);
  expect(result.current.inspectorWidth).toBe(440);
});
