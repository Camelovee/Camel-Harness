import { useEffect, useRef, useState } from "react";

import type { ApprovalStatus, Message, Session } from "../../shared/model";
import {
  reasoningFromBlocks,
  renderMarkdown,
  textFromBlocks,
} from "../../shared/presentation";

type Props = {
  activeSession: Session | undefined;
  activeWorkspace: string | null;
  messages: Message[];
  running: boolean;
  streamAssistantId: string | null;
  resolvingApprovalId: string | null;
  onResolveApproval: (requestId: string, approved: boolean) => void;
  onStart: () => void;
};

export function MessageList({
  activeSession,
  activeWorkspace,
  messages,
  running,
  streamAssistantId,
  resolvingApprovalId,
  onResolveApproval,
  onStart,
}: Props) {
  const scrollRef = useRef<HTMLElement>(null);
  const pinnedToBottom = useRef(true);

  useEffect(() => {
    pinnedToBottom.current = true;
  }, [activeSession?.session_id]);

  useEffect(() => {
    const element = scrollRef.current;
    if (element && pinnedToBottom.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [messages]);

  return (
    <section
      className="messages"
      ref={scrollRef}
      onScroll={() => {
        const element = scrollRef.current;
        if (element) {
          pinnedToBottom.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < 72;
        }
      }}
    >
      <div className="messages-inner">
        {!activeSession && (
          <Welcome activeWorkspace={activeWorkspace} onStart={onStart} />
        )}
        {messages.map((message) => {
          if (message.role === "toolResult") {
            return <ToolMessage key={message.id} message={message} />;
          }
          if (message.role === "approval") {
            return (
              <ApprovalMessage
                key={message.id}
                message={message}
                onResolve={onResolveApproval}
                resolving={resolvingApprovalId === message.approval?.request_id}
              />
            );
          }
          return (
            <ConversationMessage
              key={message.id}
              message={message}
              streaming={
                message.role === "assistant" &&
                running &&
                message.id === streamAssistantId
              }
            />
          );
        })}
      </div>
    </section>
  );
}

function Welcome({
  activeWorkspace,
  onStart,
}: {
  activeWorkspace: string | null;
  onStart: () => void;
}) {
  return (
    <div className="welcome">
      <div className="welcome-icon">🐫</div>
      <div className="eyebrow">CAMEL HARNESS · PYTHON EDITION</div>
      <h1>开始新对话</h1>
      <p>
        {activeWorkspace
          ? `直接输入消息即可开始，新的会话默认在 ${activeWorkspace} 中工作。也可以从左侧选择其他本地工作区。`
          : "直接输入消息即可开始；也可以从左侧选择本地工作区。"}
      </p>
      <button className="welcome-action" onClick={onStart}>
        创建空会话 <span>→</span>
      </button>
    </div>
  );
}

function ConversationMessage({
  message,
  streaming,
}: {
  message: Message;
  streaming: boolean;
}) {
  const content = textFromBlocks(message.content);
  const reasoning = reasoningFromBlocks(message.content);
  return (
    <article className={`message ${message.role}`}>
      {streaming && <div className="message-meta">正在输出…</div>}
      <div className="message-content">
        {message.role === "assistant" && reasoning && (
          <ThinkingMessage
            content={reasoning}
            streaming={streaming}
            hasAnswer={Boolean(content)}
          />
        )}
        {message.role === "assistant" && content ? (
          <div
            className="markdown-body"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
          />
        ) : (
          content ||
          (streaming && !reasoning ? <span className="waiting-dots">· · ·</span> : "")
        )}
      </div>
    </article>
  );
}

function ThinkingMessage({
  content,
  streaming,
  hasAnswer,
}: {
  content: string;
  streaming: boolean;
  hasAnswer: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const startedAt = useRef(0);
  const detailRef = useRef<HTMLDivElement>(null);
  const thinking = streaming && !hasAnswer;

  useEffect(() => {
    if (thinking) {
      if (!startedAt.current) startedAt.current = Date.now();
      setOpen(true);
      return;
    }
    if (startedAt.current) {
      setElapsed(
        (current) =>
          current ?? Math.max(1, Math.round((Date.now() - startedAt.current) / 1000)),
      );
      setOpen(false);
    }
  }, [thinking]);

  useEffect(() => {
    const element = detailRef.current;
    if (element && thinking && open) element.scrollTop = element.scrollHeight;
  }, [content, thinking, open]);

  const label = thinking ? "思考中…" : elapsed ? `思考了 ${elapsed} 秒` : "思考过程";

  return (
    <details
      className="thinking-message"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span className="thinking-chevron">›</span>
        <span className={`thinking-label ${thinking ? "thinking-shimmer" : ""}`}>
          {label}
        </span>
      </summary>
      <div className="thinking-detail" ref={detailRef}>
        {content}
      </div>
    </details>
  );
}

function ToolMessage({ message }: { message: Message }) {
  const block = message.content[0];
  return (
    <details className={`tool-message ${block?.is_error ? "error" : ""}`}>
      <summary>
        <span className="tool-chevron">›</span>
        <span className="tool-symbol">⌁</span>
        <strong>{block?.name ?? "工具调用"}</strong>
        <span className="tool-state">{block?.is_error ? "失败" : "已完成"}</span>
      </summary>
      <div className="tool-detail">{block?.content ?? "无结果"}</div>
    </details>
  );
}

function ApprovalMessage({
  message,
  onResolve,
  resolving,
}: {
  message: Message;
  onResolve: (requestId: string, approved: boolean) => void;
  resolving: boolean;
}) {
  const approval = message.approval;
  if (!approval) return null;
  const statusLabel: Record<ApprovalStatus, string> = {
    pending: "等待确认",
    approved: "已允许",
    rejected: "已拒绝",
    completed: "已执行",
    failed: "执行失败",
  };
  return (
    <article className={`approval-message approval-${approval.status}`}>
      <div className="approval-message-head">
        <span className="approval-message-icon">⚠</span>
        <div>
          <strong>工具请求</strong>
          <small>{approval.tool_name}</small>
        </div>
        <span className="approval-message-status">{statusLabel[approval.status]}</span>
      </div>
      <p>{approval.reason || "该工具可能修改当前环境"}</p>
      <details className="approval-arguments">
        <summary>查看调用参数</summary>
        <pre>{JSON.stringify(approval.arguments ?? {}, null, 2)}</pre>
      </details>
      {approval.status === "pending" && (
        <div className="approval-message-actions">
          <button disabled={resolving} onClick={() => onResolve(approval.request_id, false)}>
            拒绝
          </button>
          <button
            className="allow"
            disabled={resolving}
            onClick={() => onResolve(approval.request_id, true)}
          >
            {resolving ? "处理中…" : "允许执行"}
          </button>
        </div>
      )}
    </article>
  );
}
