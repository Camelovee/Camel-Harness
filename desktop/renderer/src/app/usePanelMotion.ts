import { useCallback, useEffect, useRef } from "react";
import { flushSync } from "react-dom";

export function usePanelMotion() {
  const panelRef = useRef<HTMLElement>(null);
  const animationRef = useRef<Animation | null>(null);
  useEffect(() => () => animationRef.current?.cancel(), []);

  const transition = useCallback((update: () => void) => {
    const element = panelRef.current;
    const before = element?.getBoundingClientRect();
    animationRef.current?.cancel();
    flushSync(update);
    if (!element || !before || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const after = element.getBoundingClientRect();
    // 布局只更新一次，以视觉位置和比例补间；中断时从当前画面继续，状态不依赖动画结束。
    animationRef.current = element.animate([
      { transform: `translateX(${before.left - after.left}px) scaleX(${before.width / after.width})` },
      { transform: "none" },
    ], { duration: 280, easing: "cubic-bezier(.22, 1, .36, 1)" });
  }, []);
  return { panelRef, transition };
}
