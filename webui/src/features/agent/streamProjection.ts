import type { Dispatch, SetStateAction } from "react";

import type { Activity, Approval, Message, StreamEvent } from "../../shared/model";
import { appendBlockText } from "../../shared/presentation";

type MutableValue<T> = { current: T };

type ProjectionContext = {
  activeId: string | null;
  lastSequence: MutableValue<number>;
  lastStepId: MutableValue<string | null>;
  setMessages: Dispatch<SetStateAction<Message[]>>;
  setStreamAssistantId: Dispatch<SetStateAction<string | null>>;
  setActivities: Dispatch<SetStateAction<Activity[]>>;
  setApprovals: Dispatch<SetStateAction<Approval[]>>;
  onError: (message: string) => void;
  onStatus: (message: string) => void;
};

export function projectStreamEvent(
  event: StreamEvent,
  assistantIds: Map<string, string>,
  toolIds: Map<string, string>,
  context: ProjectionContext,
): void {
  if (event.step_id && event.step_id !== context.lastStepId.current) {
    context.lastStepId.current = event.step_id;
    context.lastSequence.current = 0;
  }
  if (event.sequence <= context.lastSequence.current) return;
  context.lastSequence.current = event.sequence;
  const stepId = event.step_id ?? "unknown";
  const ensureAssistant = () => {
    const existing = assistantIds.get(stepId);
    if (existing) return existing;
    const assistantId = `assistant-${stepId}-${Date.now()}-${assistantIds.size}`;
    assistantIds.set(stepId, assistantId);
    context.setStreamAssistantId(assistantId);
    context.setMessages((current) => [
      ...current,
      { id: assistantId, role: "assistant", content: [] },
    ]);
    return assistantId;
  };

  if (event.type === "text_delta" || event.type === "reasoning_delta") {
    const assistantId = ensureAssistant();
    const type = event.type === "text_delta" ? "text" : "reasoning";
    const delta = String(event.data.text ?? "");
    context.setMessages((current) =>
      current.map((message) =>
        message.id === assistantId
          ? { ...message, content: appendBlockText(message.content, type, delta) }
          : message,
      ),
    );
    context.onStatus(event.type === "text_delta" ? "正在生成回答…" : "正在分析…");
    return;
  }
  if (event.type === "tool_call_started") {
    ensureAssistant();
    const callId = String(event.data.call_id ?? `${event.data.name}-${Date.now()}`);
    const activityId = `${callId}-${Date.now()}`;
    toolIds.set(callId, activityId);
    context.setActivities((current) => [
      ...current,
      { id: activityId, name: String(event.data.name ?? "工具"), status: "running" },
    ]);
    context.onStatus(`正在调用 ${String(event.data.name ?? "工具")}…`);
    return;
  }
  if (event.type === "tool_call_completed") {
    ensureAssistant();
    return;
  }
  if (event.type === "approval_requested") {
    projectApproval(event, ensureAssistant, context);
    return;
  }
  if (event.type === "tool_result") {
    projectToolResult(event, toolIds, context);
    return;
  }
  if (event.type === "message_completed" || event.type === "turn_completed") {
    if (event.type === "message_completed") ensureAssistant();
    context.onStatus("回答完成");
    return;
  }
  if (event.type === "turn_failed" || event.type === "stream_failed") {
    context.onError(String(event.data.error ?? "Agent 运行失败"));
    context.onStatus("运行失败");
    return;
  }
  if (event.type === "turn_cancelled" || event.type === "stream_cancelled") {
    context.onStatus("运行已取消");
  }
}

function projectApproval(
  event: StreamEvent,
  ensureAssistant: () => string,
  context: ProjectionContext,
): void {
  const requestId = String(event.data.request_id ?? "");
  if (!requestId) return;
  ensureAssistant();
  const approval: Approval = {
    request_id: requestId,
    session_id: String(event.session_id ?? context.activeId ?? ""),
    call_id: String(event.data.call_id ?? ""),
    tool_name: String(event.data.name ?? "工具"),
    capabilities: Array.isArray(event.data.capabilities)
      ? event.data.capabilities.map(String)
      : [],
    risk: String(event.data.risk ?? ""),
    arguments:
      typeof event.data.arguments === "object" && event.data.arguments !== null
        ? (event.data.arguments as Record<string, unknown>)
        : {},
    reason: String(event.data.reason ?? "该工具可能修改当前环境"),
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

function projectToolResult(
  event: StreamEvent,
  toolIds: Map<string, string>,
  context: ProjectionContext,
): void {
  const callId = String(event.data.call_id ?? "");
  const activityId = toolIds.get(callId);
  if (activityId) {
    context.setActivities((current) =>
      current.map((activity) =>
        activity.id === activityId
          ? {
              ...activity,
              status: event.data.is_error ? "error" : "success",
              result: String(event.data.content ?? ""),
            }
          : activity,
      ),
    );
  }
  context.setApprovals((current) =>
    current.filter((approval) => approval.call_id !== callId),
  );
  context.setMessages((current) => {
    const withoutApproval = current.filter(
      (message) => !(message.role === "approval" && message.approval?.call_id === callId),
    );
    if (
      withoutApproval.some(
        (message) =>
          message.role === "toolResult" && message.content[0]?.call_id === callId,
      )
    ) {
      return withoutApproval;
    }
    return [
      ...withoutApproval,
      {
        id: `tool-${callId}`,
        role: "toolResult",
        content: [
          {
            type: "tool_result",
            call_id: callId,
            name: String(event.data.name ?? "工具"),
            content: String(event.data.content ?? ""),
            is_error: Boolean(event.data.is_error),
          },
        ],
      },
    ];
  });
  context.onStatus("工具已返回，Agent 继续执行…");
}
