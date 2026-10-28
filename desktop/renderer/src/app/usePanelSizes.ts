import { useEffect, useState } from "react";

const minimumConversation = 420;
const dividerWidth = 8;
export const defaultPanelWidths = { sidebar: 236, inspector: 280 };
const clamp = (value: number, minimum: number, maximum: number) => Math.max(minimum, Math.min(value, maximum));

export function usePanelSizes(sidebarVisible: boolean, inspectorDocked: boolean) {
  const [widths, setWidths] = useState(defaultPanelWidths);
  const [windowWidth, setWindowWidth] = useState(window.innerWidth);
  useEffect(() => {
    const resize = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);

  // 缩窗时只收紧实际宽度，保留用户选择，重新放大或展开后恢复。
  const dividerSpace = (Number(sidebarVisible) + Number(inspectorDocked)) * dividerWidth;
  const available = windowWidth - minimumConversation - dividerSpace;
  const sidebarWidth = clamp(widths.sidebar, 200, Math.min(360, available - (inspectorDocked ? 240 : 0)));
  const inspectorMaximum = Math.min(440, available - (sidebarVisible ? sidebarWidth : 0));
  const inspectorWidth = clamp(widths.inspector, 240, inspectorMaximum);
  const sidebarMaximum = Math.min(360, available - (inspectorDocked ? inspectorWidth : 0));

  return {
    sidebarWidth, inspectorWidth, sidebarMaximum, inspectorMaximum,
    resizeSidebar: (width: number) => setWidths((current) => ({ ...current, sidebar: clamp(width, 200, sidebarMaximum) })),
    resizeInspector: (width: number) => setWidths((current) => ({ ...current, inspector: clamp(width, 240, inspectorMaximum) })),
  };
}
