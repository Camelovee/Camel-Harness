import type { AgentEvent, SubagentSnapshot, SubagentView } from "../../shared/model";
import { projectAgentEvent } from "./streamProjection";

export type SubagentStream = {
  lastSequence: { current: number };
  assistantIds: Map<string, string>;
};

export function childView(snapshot: SubagentSnapshot): SubagentView {
  return { ...snapshot, messages: [], approvals: [], streamAssistantId: null,
    live: false, loaded: false, loading: false, error: null };
}

export function projectSubagentEvent(
  view: SubagentView, event: AgentEvent, stream: SubagentStream,
): SubagentView {
  if (event.type === "subagent_event" || event.sequence <= stream.lastSequence.current) return view;
  let next: SubagentView = { ...view, live: true, loaded: true, loading: false };
  const streamAssistantId = { current: next.streamAssistantId };
  projectAgentEvent(event, stream.assistantIds, {
    activeId: view.session_id, lastSequence: stream.lastSequence, streamAssistantId,
    setMessages: (action) => { next = { ...next, messages: typeof action === "function" ? action(next.messages) : action }; },
    setApprovals: (action) => { next = { ...next, approvals: typeof action === "function" ? action(next.approvals) : action }; },
    setStreamAssistantId: (value) => { next = { ...next, streamAssistantId: typeof value === "function" ? value(next.streamAssistantId) : value }; },
    setUsage: () => undefined,
    onError: (error) => { next = { ...next, error }; },
    onStatus: (phase) => { next = { ...next, phase }; },
  });
  if (event.type === "turn_start") next.turn = event.turn;
  if (event.type === "approval_requested") next.status = "waiting_approval";
  if (event.type === "approval_resolved") next.status = "running";
  if (event.type === "agent_end") {
    next.status = event.status;
    next.approvals = [];
    next.messages = next.messages.filter((message) => message.role !== "approval");
    next.task_outcome = { status: event.status, stop_reason: event.stop_reason,
      error: event.error, finished_at: "" };
  }
  return next;
}
