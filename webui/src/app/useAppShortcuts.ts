import { useEffect, useRef } from "react";
import type { DesktopAction } from "../shared/platform";

export function useAppShortcuts(actions: Record<DesktopAction, () => void>): void {
  const actionsRef = useRef(actions);
  actionsRef.current = actions;
  useEffect(() => {
    const dispatch = (action: DesktopAction) => actionsRef.current[action]?.();
    const unsubscribe = window.camelDesktop?.onAction(dispatch);
    // Electron 由原生菜单处理快捷键，浏览器使用同一组页面操作。
    if (window.camelDesktop) return unsubscribe;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey || event.isComposing) return;
      const action = ({ n: "new-chat", o: "open-project", b: "toggle-sidebar", ",": "settings" } as const)[event.key.toLowerCase() as "n" | "o" | "b" | ","];
      if (action) { event.preventDefault(); dispatch(action); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}
