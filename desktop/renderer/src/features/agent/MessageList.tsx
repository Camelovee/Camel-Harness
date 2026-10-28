import { Fragment, useEffect, useRef, useState } from "react";
import { ArrowRight, ArrowUpRight, Bug, CaretRight, CheckCircle, Code, Compass, WarningCircle, Wrench } from "@phosphor-icons/react";
import { CamelMark } from "../../shared/CamelMark";

import type { ApprovalStatus, Message, Session, SubagentView } from "../../shared/model";
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
  onSuggest?: (prompt: string) => void;
  showToolCalls?: boolean;
  subagents?: SubagentView[];
  onOpenSubagent?: (sessionId: string) => void;
  onOpenTool?: (message: Message) => void;
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
  onSuggest,
  showToolCalls = false,
  subagents = [],
  onOpenSubagent,
  onOpenTool,
}: Props) {
  const outcomeLabels = { completed: "已完成", failed: "执行失败", cancelled: "已取消", unknown: "结果未知" };
  const scrollRef = useRef<HTMLElement>(null);
  const pinnedToBottom = useRef(true);
  const subagentsByCallId = new Map<string, SubagentView[]>();
  for (const child of subagents) {
    const siblings = subagentsByCallId.get(child.parent_tool_call_id) ?? [];
    siblings.push(child);
    subagentsByCallId.set(child.parent_tool_call_id, siblings);
  }

  useEffect(() => {
    pinnedToBottom.current = true;
  }, [activeSession?.session_id]);

  useEffect(() => {
    const element = scrollRef.current;
    if (element && pinnedToBottom.current) {
      element.scrollTop = messages.length ? element.scrollHeight : 0;
    }
  }, [messages, subagents]);

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
          <Welcome activeWorkspace={activeWorkspace} onStart={onStart} onSuggest={onSuggest} />
        )}
        {messages.map((message) => {
          if (message.role === "toolResult") {
            return <ToolMessage key={message.id} message={message} onOpen={onOpenTool} />;
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
          // 父调用决定卡片位置；历史快照和实时子事件的到达顺序不参与排序。
          const messageSubagents = message.role === "assistant"
            ? message.content.flatMap((block) => block.type === "tool_use" && block.name === "delegate_task" && block.call_id
              ? subagentsByCallId.get(block.call_id) ?? [] : [])
            : [];
          return (
            <Fragment key={message.id}>
              <ConversationMessage
                message={message}
                showToolCalls={showToolCalls}
                streaming={
                  message.role === "assistant" &&
                  running &&
                  message.id === streamAssistantId
                }
              />
              {messageSubagents.length > 0 && <div className="subagent-summaries" aria-label="委派任务">
                {messageSubagents.map((child) => <button key={child.session_id} className="subagent-summary"
                  onClick={() => onOpenSubagent?.(child.session_id)}>
                  <strong>{child.agent_type} 子任务</strong>
                  <span>{child.task}</span>
                  <small>{child.task_outcome ? outcomeLabels[child.task_outcome.status] : child.phase}{child.approvals.length > 0 ? ` · ${child.approvals.length} 项待审批` : ""} · 查看过程 ›</small>
                </button>)}
              </div>}
            </Fragment>
          );
        })}
      </div>
    </section>
  );
}

function Welcome({
  activeWorkspace,
  onStart,
  onSuggest,
}: {
  activeWorkspace: string | null;
  onStart: () => void;
  onSuggest?: (prompt: string) => void;
  showToolCalls?: boolean;
  subagents?: SubagentView[];
  onOpenSubagent?: (sessionId: string) => void;
}) {
  return (
    <div className="welcome">
      <div className="welcome-landscape" aria-hidden="true"><svg viewBox="0 0 300 110"><circle cx="222" cy="32" r="19" /><path d="M0 99Q65 24 156 87T300 72V110H0Z" /><path d="M0 104Q112 66 204 99T300 91V110H0Z" /></svg><CamelMark className="welcome-camel" /></div>
      <div className="eyebrow">A LITTLE SPACE. A GREAT NEXT STEP.</div>
      <h2>今天，想构建些什么？</h2>
      <p>从一个想法，到每一步落地。<br />Camel 与你一起探索{activeWorkspace ? "这个工作区" : "本地项目"}。</p>
      {onSuggest && <div className="welcome-prompts">
        <button onClick={() => onSuggest("帮我理解这个项目的结构和主要运行流程")}><Compass size={18} aria-hidden="true" /><span>理解项目<small>找到清晰的起点</small></span><ArrowUpRight size={14} aria-hidden="true" /></button>
        <button onClick={() => onSuggest("帮我排查项目中的问题，先分析原因，再给出修复建议")}><Bug size={18} aria-hidden="true" /><span>定位问题<small>一起解开症结</small></span><ArrowUpRight size={14} aria-hidden="true" /></button>
        <button onClick={() => onSuggest("我想实现一个新功能，请先分析现有代码并和我确认方案")}><Code size={18} aria-hidden="true" /><span>实现想法<small>从下一步开始</small></span><ArrowUpRight size={14} aria-hidden="true" /></button>
      </div>}
      <button className="welcome-action" onClick={onStart}>
        创建空会话 <ArrowRight size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

function ConversationMessage({
  message,
  streaming,
  showToolCalls,
}: {
  message: Message;
  streaming: boolean;
  showToolCalls: boolean;
}) {
  const content = textFromBlocks(message.content);
  const reasoning = reasoningFromBlocks(message.content);
  return (
    <article className={`message ${message.role}`}>
      {message.role === "assistant" && (content || reasoning || streaming) && <div className="assistant-identity"><span><CamelMark /></span><strong>Camel</strong><small>与你协作</small></div>}
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
      {showToolCalls && message.content.filter((block) => block.type === "tool_use").map((block) => (
        <details className="tool-message" key={block.call_id}>
          <summary>调用 {block.name ?? "工具"}</summary>
          <pre className="tool-detail">{block.arguments ? JSON.stringify(block.arguments, null, 2) : block.arguments_text ?? "等待参数…"}</pre>
        </details>
      ))}
      {message.role === "assistant" && !streaming && (message.model || message.usage) && (
        <div className="message-meta">
          {[message.provider, message.response_model ?? message.model].filter(Boolean).join(" / ")}
          {message.usage && (
            <> · 输入 {message.usage.input_tokens?.toLocaleString() ?? "未知"}
              {" · 输出 "}{message.usage.output_tokens?.toLocaleString() ?? "未知"} Token</>
          )}
        </div>
      )}
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
        <CaretRight className="thinking-chevron" size={14} aria-hidden="true" />
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

function ToolMessage({ message, onOpen }: { message: Message; onOpen?: (message: Message) => void }) {
  const block = message.content[0];
  const statusLabels = {
    not_executed: "未执行", succeeded: "已完成", failed: "执行失败", unknown: "结果未知",
  };
  const executionStatus = block?.details?.execution_status;
  const statusLabel = executionStatus ? statusLabels[executionStatus] : block?.is_error ? "失败" : "已完成";
  return (
    <details className={`tool-message ${block?.is_error ? "error" : ""}`}>
      <summary>
        <CaretRight className="tool-chevron" size={14} aria-hidden="true" />
        {executionStatus === "succeeded" || (!executionStatus && !block?.is_error) ? <CheckCircle className="tool-symbol" size={18} aria-hidden="true" /> : <Wrench className="tool-symbol" size={18} aria-hidden="true" />}
        <strong>{block?.name ?? "工具调用"}</strong>
        <span className="tool-state">{statusLabel}</span>
      </summary>
      <div className="tool-detail">{block?.content ?? "无结果"}</div>
      {onOpen && <button className="tool-open" onClick={() => onOpen(message)}>在侧栏查看</button>}
      {block?.details?.content_ref && (
        <div className="tool-detail">
          完整结果：{block.details.content_ref.path}
          {" · "}{block.details.content_ref.size_bytes.toLocaleString()} 字节
        </div>
      )}
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
        <span className="approval-message-icon"><WarningCircle size={21} aria-hidden="true" /></span>
        <div>
          <strong>工具请求</strong>
          <small>{approval.tool_name}</small>
        </div>
        <span className="approval-message-status">{statusLabel[approval.status]}</span>
      </div>
      <p>{approval.reason || "该工具可能修改当前环境"}</p>
      {approval.execution_context?.command !== undefined && (
        <div className="approval-execution">
          <strong>工作目录</strong>
          <pre>{approval.execution_context.working_directory}</pre>
          <strong>完整命令</strong>
          <pre>{approval.execution_context.command}</pre>
        </div>
      )}
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
