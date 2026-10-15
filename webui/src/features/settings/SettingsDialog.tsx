import { useEffect, useRef, useState } from "react";

import type { ModelInput, WebSettings } from "../../shared/model";
import { ModelSettingsPanel } from "./ModelSettingsPanel";
import { PlaceholderPanel } from "./PlaceholderPanel";

type Section = "models" | "skills" | "mcp";

type Props = {
  settings: WebSettings | null;
  saving: boolean;
  onClose: () => void;
  onSavePreferences: (modelId: string, thinking: boolean) => Promise<boolean>;
  onSaveModel: (modelId: string | null, body: ModelInput) => Promise<boolean>;
  onDeleteModel: (modelId: string) => Promise<boolean>;
};

const SECTIONS: { id: Section; label: string }[] = [
  { id: "models", label: "模型" },
  { id: "skills", label: "Skills" },
  { id: "mcp", label: "MCP" },
];

export function SettingsDialog({
  settings,
  saving,
  onClose,
  onSavePreferences,
  onSaveModel,
  onDeleteModel,
}: Props) {
  const [section, setSection] = useState<Section>("models");
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="modal-backdrop settings-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <header className="settings-header">
          <div>
            <span className="eyebrow">PREFERENCES</span>
            <h2 id="settings-title">设置</h2>
          </div>
          <button ref={closeRef} type="button" className="modal-close" onClick={onClose} aria-label="关闭设置">×</button>
        </header>

        {!settings ? (
          <p className="settings-empty">正在加载设置…</p>
        ) : (
          <div className="settings-body">
            <nav className="settings-nav" aria-label="设置分类">
              {SECTIONS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`settings-nav-item ${section === item.id ? "active" : ""}`}
                  aria-current={section === item.id ? "page" : undefined}
                  onClick={() => setSection(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </nav>
            <div className="settings-content">
              {section === "models" && (
                <ModelSettingsPanel
                  settings={settings}
                  saving={saving}
                  onSavePreferences={onSavePreferences}
                  onSaveModel={onSaveModel}
                  onDeleteModel={onDeleteModel}
                />
              )}
              {section === "skills" && (
                <PlaceholderPanel
                  title="Skills"
                  description="Skills 从工作区与 ~/.camel 目录按 SKILL.md 约定发现，运行时在系统提示中按需加载。"
                />
              )}
              {section === "mcp" && (
                <PlaceholderPanel
                  title="MCP"
                  description="MCP Server 通过工作区下的 .mcp.json 配置，启动后端时连接并注册其工具。"
                />
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
