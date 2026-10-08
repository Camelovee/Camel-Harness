import type { Dispatch, SetStateAction } from "react";

import type { AgentEvent, Approval, Message, TokenUsage } from "../../shared/model";
import { appendBlockText } from "../../shared/presentation";

type MutableValue<T> = { current: T };

type ProjectionContext = {
  activeId: string | null;
  lastSequence: MutableValue<number>;
  streamAssistantId: MutableValue<string | null>;
  setMessages: Dispatch<SetStateAction<Message[]>>;
  setStreamAssistantId: Dispatch<SetStateAction<string | null>>;
  setUsage: Dispatch<SetStateAction<TokenUsage>>;
  setApprovals: Dispatch<SetStateAction<Approval[]>>;
  onError: (message: string) => void;
  onStatus: (message: string) => void;
};

export function projectAgentEvent(
  event: AgentEvent,
  assistantIds: Map<string, string>,
  context: ProjectionContext,
): void {
  if (event.sequence <= context.lastSequence.current) return;
  context.lastSequence.current = event.sequence;

  const ensureAssistant = (messageId: string) => {
    const existing = assistantIds.get(messageId);
    if (existing) return existing;
    const assistantId = `assistant-${messageId}`;
    assistantIds.set(messageId, assistantId);
    context.streamAssistantId.current = assistantId;
    context.setStreamAssistantId(assistantId);
    context.setMessages((current) => [
      ...current,
      { id: assistantId, role: "assistant", content: [] },
    ]);
    return assistantId;
  };

  if (event.type === "agent_start") {
    context.onStatus("Agent 正在运行…");
    return;
  }
  if (event.type === "turn_start") {
    context.onStatus(`正在处理第 ${event.turn} 个 Turn…`);
    return;
  }
  if (event.type === "message_start") {
    if (event.role === "assistant") ensureAssistant(event.message_id);
    return;
  }
  if (event.type === "message_update") {
    if (event.assistant_event.type === "usage_updated") {
      const data = event.assistant_event.usage;
      context.setUsage((current) => ({
        ...current,
        input: typeof data.input_tokens === "number" ? data.input_tokens : current.input,
        output: typeof data.output_tokens === "number" ? data.output_tokens : current.output,
      }));
      return;
    }
    projectMessageUpdate(event, ensureAssistant, context);
    return;
  }
  if (event.type === "tool_execution_start") {
    context.onStatus(`正在调用 ${event.name}…`);
    return;
  }
  if (event.type === "approval_requested") {
    projectApproval(event, context);
    return;
  }
  if (event.type === "tool_execution_end") {
    projectToolExecutionEnd(event, context);
    return;
  }
  if (event.type === "message_end") {
    if (event.message.role === "user") {
      appendUserMessage(event.message_id, event.message.content, context);
    }
    if (event.message.role === "assistant") {
      const assistantId = ensureAssistant(event.message_id);
      const content = event.message.content;
      const { provider, model, response_model, usage } = event.message;
      context.setMessages((current) => current.map((message) =>
        message.id === assistantId ? { ...message, content, provider, model, response_model, usage } : message,
      ));
      context.streamAssistantId.current = null;
      context.setStreamAssistantId(null);
    }
    if (event.message.role === "toolResult") {
      appendToolResultMessage(event.message_id, event.message, context);
    }
    return;
  }
  if (event.type === "turn_end") {
    context.onStatus("Turn 已完成");
    return;
  }
  if (event.type === "agent_end") {
    context.setUsage((current) => ({ ...current, runTotal: event.total_tokens }));
    const incompleteId = context.streamAssistantId.current;
    if (incompleteId) {
      context.setMessages((current) => current.filter((message) => message.id !== incompleteId));
      context.streamAssistantId.current = null;
      context.setStreamAssistantId(null);
    }
    if (event.status === "failed") {
      context.onError(event.error || "Agent 运行失败");
      context.onStatus("运行失败");
    } else if (event.status === "cancelled") {
      context.onStatus("运行已取消");
    } else {
      context.onStatus("回答完成");
    }
  }
}

function appendUserMessage(
  messageId: string,
  content: string,
  context: ProjectionContext,
): void {
  context.setMessages((current) =>
    current.some((message) => message.id === messageId)
      ? current
      : [
          ...current,
          { id: messageId, role: "user", content: [{ type: "text", text: content }] },
        ],
  );
}

function projectMessageUpdate(
  event: Extract<AgentEvent, { type: "message_update" }>,
  ensureAssistant: (messageId: string) => string,
  context: ProjectionContext,
): void {
  const update = event.assistant_event;
  if (update.type !== "text_delta" && update.type !== "reasoning_delta") return;
  const assistantId = ensureAssistant(event.message_id);
  const type = update.type === "text_delta" ? "text" : "reasoning";
  const delta = update.text;
  context.setMessages((current) =>
    current.map((message) =>
      message.id === assistantId
        ? { ...message, content: appendBlockText(message.content, type, delta) }
        : message,
    ),
  );
  context.onStatus(update.type === "text_delta" ? "正在生成回答…" : "正在分析…");
}

function projectApproval(
  event: Extract<AgentEvent, { type: "approval_requested" }>,
  context: ProjectionContext,
): void {
  const approval: Approval = {
    request_id: event.request_id,
    session_id: event.session_id || context.activeId || "",
    call_id: event.call_id,
    tool_name: event.name,
    capabilities: event.capabilities,
    risk: event.risk,
    arguments: event.arguments,
    reason: event.reason,
    status: "pending",
  };
  context.setMessages((current) =>
    current.some(
      (message) =>
        message.role === "approval" && message.approval?.request_id === approval.request_id,
    )
      ? current
      : [
          ...current,
          { id: `approval-${approval.request_id}`, role: "approval", content: [], approval },
        ],
  );
  context.setApprovals((current) =>
    current.some((item) => item.request_id === approval.request_id)
      ? current
      : [...current, approval],
  );
  context.onStatus("等待工具审批…");
}

function projectToolExecutionEnd(
  event: Extract<AgentEvent, { type: "tool_execution_end" }>,
  context: ProjectionContext,
): void {
  const result = event.result;
  context.setApprovals((current) =>
    current.filter((approval) => approval.call_id !== result.call_id),
  );
}

function appendToolResultMessage(
  messageId: string,
  message: Extract<
    Extract<AgentEvent, { type: "message_end" }>["message"],
    { role: "toolResult" }
  >,
  context: ProjectionContext,
): void {
  context.setMessages((current) => {
    const withoutApproval = current.filter(
      (item) =>
        !(item.role === "approval" && item.approval?.call_id === message.tool_call_id),
    );
    if (withoutApproval.some((item) => item.id === messageId)) return withoutApproval;
    return [
      ...withoutApproval,
      {
        id: messageId,
        role: "toolResult",
        content: [
          {
            type: "tool_result",
            call_id: message.tool_call_id,
            name: message.tool_name,
            content: message.content,
            is_error: message.is_error,
            details: message.details,
          },
        ],
      },
    ];
  });
  context.onStatus("工具已返回，Agent 继续执行…");
}
