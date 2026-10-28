import { ArrowCounterClockwise, Minus, Plus } from "@phosphor-icons/react";
import { useRef, useState } from "react";

type Props = {
  side: "sidebar" | "inspector";
  width: number;
  minimum: number;
  maximum: number;
  defaultWidth: number;
  onResize: (width: number) => void;
  onCollapse: () => void;
};

export function PanelDivider({ side, width, minimum, maximum, defaultWidth, onResize, onCollapse }: Props) {
  const label = side === "sidebar" ? "侧栏" : "执行详情";
  const direction = side === "sidebar" ? 1 : -1;
  const popoverRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; width: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const [resizing, setResizing] = useState(false);
  const [popoverLeft, setPopoverLeft] = useState(0);
  function finishDrag() {
    if (!dragRef.current) return;
    suppressClick.current = dragRef.current.moved;
    dragRef.current = null;
    setResizing(false);
  }

  return <>
    <button type="button" role="separator" tabIndex={0} className={`panel-divider ${side}-divider`}
      aria-label={`${label}宽度`} aria-orientation="vertical" aria-controls={side === "sidebar" ? "workspace-navigation" : "execution-inspector"}
      aria-valuemin={minimum} aria-valuemax={maximum} aria-valuenow={Math.round(width)} aria-valuetext={`${Math.round(width)} 像素`}
      title="拖动调整宽度 · 双击复位 · 方向键微调 · 点击更多操作" data-resizing={resizing}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        dragRef.current = { x: event.clientX, width, moved: false };
        suppressClick.current = false;
      }}
      onPointerMove={(event) => {
        const drag = dragRef.current;
        if (!drag) return;
        if (!drag.moved && Math.abs(event.clientX - drag.x) < 3) return;
        drag.moved = true;
        setResizing(true);
        onResize(drag.width + (event.clientX - drag.x) * direction);
      }}
      onPointerUp={finishDrag} onPointerCancel={finishDrag} onLostPointerCapture={finishDrag}
      onClick={(event) => {
        if (suppressClick.current) { suppressClick.current = false; return; }
        setPopoverLeft(Math.max(12, Math.min(event.currentTarget.getBoundingClientRect().left - 90, window.innerWidth - 202)));
        popoverRef.current?.togglePopover();
      }}
      onDoubleClick={() => { popoverRef.current?.hidePopover(); onResize(defaultWidth); }}
      onKeyDown={(event) => {
        const step = event.shiftKey ? 32 : 16;
        if (event.key === "ArrowLeft") onResize(width - step * direction);
        else if (event.key === "ArrowRight") onResize(width + step * direction);
        else if (event.key === "Home") onResize(minimum);
        else if (event.key === "End") onResize(maximum);
        else if (event.key === "Enter") onCollapse();
        else return;
        event.preventDefault();
        event.stopPropagation();
      }} />
    <div ref={popoverRef} popover="auto" className="divider-popover" style={{ left: popoverLeft }} aria-label={`调整${label}宽度`}>
      <span>{label} · {Math.round(width)} px</span>
      <div><button className="icon-button" aria-label={`缩小${label}`} disabled={width <= minimum} onClick={() => onResize(width - 16)}><Minus size={16} aria-hidden="true" /></button>
        <button className="icon-button" aria-label={`增大${label}`} disabled={width >= maximum} onClick={() => onResize(width + 16)}><Plus size={16} aria-hidden="true" /></button>
        <button className="icon-button" aria-label={`重置${label}宽度`} onClick={() => onResize(defaultWidth)}><ArrowCounterClockwise size={16} aria-hidden="true" /></button></div>
    </div>
  </>;
}
