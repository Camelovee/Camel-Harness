import { useEffect, useRef } from "react";
import type { Message } from "../../shared/model";

export function ToolDetailsPanel({ message, onClose }: { message: Message; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);
  const block = message.content[0];
  return <aside className="detail-panel tool-panel" aria-label="工具详情">
    <header className="subagent-panel-head"><strong>{block?.name ?? "工具"} · 执行结果</strong>
      <button ref={closeRef} aria-label="关闭工具详情" onClick={onClose}>×</button>
    </header>
    <div className="tool-panel-content">
      <pre>{block?.content ?? "无结果"}</pre>
      {block?.details?.content_ref && <p>完整结果保存在 {block.details.content_ref.path}
        {" · "}{block.details.content_ref.size_bytes.toLocaleString()} 字节</p>}
    </div>
  </aside>;
}
