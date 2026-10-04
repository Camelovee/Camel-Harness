import { useEffect, useRef, useState } from "react";

import type { ModelProfile, PermissionMode } from "../../shared/model";
import { workspaceName } from "../../shared/presentation";

type Props = {
  active: boolean;
  activeWorkspace: string | null;
  running: boolean;
  switchingModel: boolean;
  status: string;
  models: ModelProfile[];
  selectedModelId: string;
  thinking: boolean;
  onModelChange: (modelId: string) => void;
  onSend: (
    prompt: string,
    permissionMode: PermissionMode,
    thinking: boolean,
    modelId: string,
  ) => Promise<boolean>;
  canResume: boolean;
  onAbort: () => Promise<void>;
  onResume: (permissionMode: PermissionMode, thinking: boolean, modelId: string) => Promise<void>;
};

export function Composer({
  active,
  activeWorkspace,
  running,
  switchingModel,
  status,
  models,
  selectedModelId,
  thinking,
  onModelChange,
  onSend,
  canResume,
  onAbort,
  onResume,
}: Props) {
  const [prompt, setPrompt] = useState("");
  const [permissionMode, setPermissionMode] = useState<PermissionMode>("default");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = inputRef.current;
    if (!element) return;
    element.style.height = "0px";
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`;
  }, [prompt]);

  async function submit() {
    const value = prompt.trim();
    if (!value || !active || !selectedModelId || switchingModel) return;
    if (await onSend(value, permissionMode, thinking, selectedModelId)) setPrompt("");
    inputRef.current?.focus();
  }

  return (
    <div className="composer-area">
      <div className={`composer-box ${!active ? "disabled" : ""}`}>
        <textarea
          ref={inputRef}
          disabled={!active || !selectedModelId}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder={!active ? "正在准备本地工作区" : selectedModelId ? "输入消息，开始与 Agent 协作…" : "请先在设置中配置模型"}
          rows={2}
        />
        <div className="composer-toolbar">
          <span className="composer-caption">
            {activeWorkspace ? workspaceName(activeWorkspace) : "用户主目录"}
          </span>
          <div className="composer-right">
            <select
              className="model-select"
              aria-label="选择模型"
              value={selectedModelId}
              onChange={(event) => onModelChange(event.target.value)}
              disabled={!active || running || switchingModel || !models.length}
              title="选择当前或下一次对话的模型"
            >
              {!selectedModelId && <option value="">配置模型</option>}
              {models.map((profile) => (
                <option key={profile.id} value={profile.id} disabled={!profile.configured}>
                  {profile.name} · {profile.model}{!profile.configured ? " · 未配置" : ""}
                </option>
              ))}
            </select>
            <select
              value={permissionMode}
              onChange={(event) => setPermissionMode(event.target.value as PermissionMode)}
              disabled={!active}
            >
              <option value="default">默认权限</option>
              <option value="plan">Plan Mode</option>
              <option value="accept_edits">接受编辑</option>
              <option value="yolo">YOLO</option>
            </select>
            <button
              type="button"
              className="send-button"
              disabled={!prompt.trim() || !active || !selectedModelId || switchingModel}
              onClick={() => void submit()}
              title={running ? "追加运行中输入" : "发送消息"}
            >
              {running ? "↳" : "↑"}
            </button>
            {running && (
              <button
                type="button"
                className="stop-button"
                onClick={() => void onAbort()}
                title="暂停当前 Run"
                aria-label="暂停当前 Run"
              >
                <span className="stop-icon" aria-hidden="true" />
              </button>
            )}
            {!running && canResume && (
              <button
                type="button"
                onClick={() => void onResume(permissionMode, thinking, selectedModelId)}
                disabled={!selectedModelId || switchingModel}
                title="从最后一个完整消息边界继续"
              >
                继续
              </button>
            )}
          </div>
        </div>
      </div>
      <div className="composer-hint">
        <span>Enter 发送 · Shift + Enter 换行</span>
        <span className={running ? "status-running" : ""}>{status}</span>
      </div>
    </div>
  );
}
