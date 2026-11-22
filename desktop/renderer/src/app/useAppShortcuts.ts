import { useEffect, useRef } from "react";
import type { DesktopAction } from "../shared/platform";

export function useAppShortcuts(actions: Record<DesktopAction, () => void>): void {
  const actionsRef = useRef(actions);
  actionsRef.current = actions;
  useEffect(() => {
    return window.camelDesktop.onAction((action) => actionsRef.current[action]());
  }, []);
}
