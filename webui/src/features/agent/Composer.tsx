import { useEffect, useRef } from "react";
import type { Dispatch, SetStateAction } from "react";

import type { ModelProfile, PermissionMode } from "../../shared/model";
import { workspaceName } from "../../shared/presentation";

type Props = {
  active: boolean;
  activeWorkspace: string | null;
  running: boolean;
  switchingModel: boolean;
  status: string;
  prompt: string;
  onPromptChange: Dispatch<SetStateAction<string>>;
  permissionMode: PermissionMode;
  onPermissionModeChange: (value: PermissionMode) => void;
  starting: boolean;
  models: ModelProfile[];
  selectedModelId: string;
  thinking: boolean;
  onModelChange: (modelId: string) => void;
  choosingWorkspace: boolean;
  onChooseWorkspace: () => void;
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
  prompt,
  onPromptChange,
  permissionMode,
  onPermissionModeChange,
  starting,
  models,
  selectedModelId,
  thinking,
  onModelChange,
  choosingWorkspace,
  onChooseWorkspace,
  onSend,
  canResume,
  onAbort,
  onResume,
}: Props) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  const ignoreEnterRef = useRef(false);

  useEffect(() => {
    const element = inputRef.current;
    if (!element) return;
    element.style.height = "0px";
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`;
  }, [prompt]);

  async function submit() {
    const value = prompt.trim();
    if (!value || !active || !selectedModelId || switchingModel || starting) return;
    const submittedPrompt = prompt;
    if (await onSend(value, permissionMode, thinking, selectedModelId)) {
      onPromptChange((current) => current === submittedPrompt ? "" : current);
    }
  }

  return (
    <div className="composer-area">
      <div className={`composer-box ${!active ? "disabled" : ""}`}>
        <textarea
          ref={inputRef}
          disabled={!active || !selectedModelId}
          value={prompt}
          onChange={(event) => onPromptChange(event.target.value)}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={() => {
            composingRef.current = false;
            ignoreEnterRef.current = true;
            window.setTimeout(() => { ignoreEnterRef.current = false; }, 0);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              if (event.nativeEvent.isComposing || composingRef.current || ignoreEnterRef.current || event.keyCode === 229) return;
              event.preventDefault();
              void submit();
            }
          }}
          placeholder={!active ? "正在准备本地工作区" : selectedModelId ? "输入消息，开始与 Agent 协作…" : "请先在设置中配置模型"}
          rows={2}
        />
        <div className="composer-toolbar">
          <button type="button" className="composer-directory" onClick={onChooseWorkspace} disabled={choosingWorkspace || running} title={`选择新会话的工作目录；当前：${activeWorkspace ?? "用户主目录"}`} aria-label="选择新会话工作目录">
            <span aria-hidden="true">▱</span>
            <span>{choosingWorkspace ? "正在选择…" : activeWorkspace ? workspaceName(activeWorkspace) : "用户主目录"}</span>
            <span aria-hidden="true">⌄</span>
          </button>
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
              onChange={(event) => onPermissionModeChange(event.target.value as PermissionMode)}
              disabled={!active || running}
            >
              <option value="default">默认权限</option>
              <option value="plan">Plan Mode</option>
              <option value="accept_edits">接受编辑</option>
              <option value="yolo">YOLO</option>
            </select>
            <button
              type="button"
              className="send-button"
              disabled={!prompt.trim() || !active || !selectedModelId || switchingModel || starting}
              onClick={() => void submit()}
              title={running ? "追加运行中输入" : "发送消息"}
            >
              {running ? "↳" : "↑"}
            </button>
            {running && (
              <button
                type="button"
                className="stop-button"
                disabled={starting}
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
