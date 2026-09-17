import { useEffect, useRef, useState } from "react";

import type { PermissionMode } from "../../shared/model";
import { workspaceName } from "../../shared/presentation";

type Props = {
  active: boolean;
  activeWorkspace: string | null;
  running: boolean;
  status: string;
  onSend: (
    prompt: string,
    permissionMode: PermissionMode,
    thinking: boolean,
  ) => Promise<boolean>;
};

export function Composer({
  active,
  activeWorkspace,
  running,
  status,
  onSend,
}: Props) {
  const [prompt, setPrompt] = useState("");
  const [permissionMode, setPermissionMode] = useState<PermissionMode>("default");
  const [thinking, setThinking] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = inputRef.current;
    if (!element) return;
    element.style.height = "0px";
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`;
  }, [prompt]);

  async function submit() {
    const value = prompt.trim();
    if (!value || !active || running) return;
    setPrompt("");
    await onSend(value, permissionMode, thinking);
    inputRef.current?.focus();
  }

  return (
    <div className="composer-area">
      <div className={`composer-box ${!active ? "disabled" : ""}`}>
        <textarea
          ref={inputRef}
          disabled={!active}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder={active ? "输入消息，开始与 Agent 协作…" : "先选择工作区并创建会话"}
          rows={2}
        />
        <div className="composer-toolbar">
          <span className="composer-caption">
            {activeWorkspace ? workspaceName(activeWorkspace) : "未选择工作区"}
          </span>
          <div className="composer-right">
            <button
              type="button"
              className={`thinking-toggle ${thinking ? "active" : ""}`}
              aria-pressed={thinking}
              disabled={!active || running}
              onClick={() => setThinking((current) => !current)}
              title="请求支持的模型返回思考过程"
            >
              ✦ 思考
            </button>
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
              className="send-button"
              disabled={!prompt.trim() || !active || running}
              onClick={() => void submit()}
            >
              {running ? "…" : "↑"}
            </button>
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
