import { useId, type ReactNode } from "react";
import { Info } from "@phosphor-icons/react";

export function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  const contentId = useId();

  return (
    <span className="info-tip">
      <button type="button" className="info-tip-trigger" popoverTarget={contentId}
        aria-label={label} aria-describedby={contentId} title={label}>
        <Info size={15} aria-hidden="true" />
      </button>
      <span id={contentId} className="info-tip-content" popover="auto">{children}</span>
    </span>
  );
}
