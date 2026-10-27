import { apiJson, apiPath } from "../../shared/api";
import type {
  AgentEvent,
  Approval,
  Message,
  PermissionMode,
  ThinkingEffort,
  WireMessage,
  SubagentSnapshot,
} from "../../shared/model";

export async function fetchConversation(sessionId: string): Promise<Message[]> {
  const result = await apiJson<{ messages: WireMessage[] }>(`/sessions/${sessionId}/messages`);
  return result.messages
    .map(projectStoredMessage)
    .filter((message): message is Message => message !== null);
}

function projectStoredMessage(message: WireMessage, index: number): Message | null {
  if (message.role === "user") {
    return {
      id: `user-${index}-${message.content.length}`,
      role: "user",
      content: [{ type: "text", text: message.content }],
    };
  }
  if (message.role === "assistant") {
    return {
      id: `assistant-${index}-${message.content.length}`,
      role: "assistant",
      content: message.content,
      provider: message.provider,
      model: message.model,
      response_model: message.response_model,
      usage: message.usage,
    };
  }
  if (message.role === "toolResult") {
    return {
      id: `tool-${message.tool_call_id}`,
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
    };
  }
  return null;
}

export async function fetchPendingApprovals(): Promise<Approval[]> {
  const result = await apiJson<{ approvals: Omit<Approval, "status">[] }>("/approvals");
  return result.approvals.map((approval) => ({ ...approval, status: "pending" }));
}

export async function submitApproval(requestId: string, approved: boolean): Promise<void> {
  await apiJson(`/approvals/${requestId}`, {
    method: "POST",
    body: JSON.stringify({ approved }),
  });
}

type RunRequest = {
  prompt: string;
  permissionMode: PermissionMode;
  effort: ThinkingEffort;
  modelId: string;
};

type StreamOptions = {
  signal?: AbortSignal;
  onConnected?: () => void;
};

export async function streamAgentRun(
  sessionId: string,
  request: RunRequest,
  onEvent: (event: AgentEvent) => void,
  options: StreamOptions = {},
): Promise<void> {
  await streamAgentResponse(`/sessions/${sessionId}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      prompt: request.prompt,
      permission_mode: request.permissionMode,
      effort: request.effort,
      model_id: request.modelId,
    }),
    signal: options.signal,
  }, onEvent, options.onConnected);
}

export async function streamAgentResume(
  sessionId: string,
  request: Omit<RunRequest, "prompt">,
  onEvent: (event: AgentEvent) => void,
  options: StreamOptions = {},
): Promise<void> {
  await streamAgentResponse(`/sessions/${sessionId}/runs/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      permission_mode: request.permissionMode,
      effort: request.effort,
      model_id: request.modelId,
    }),
    signal: options.signal,
  }, onEvent, options.onConnected);
}

export async function steerAgentRun(sessionId: string, content: string): Promise<void> {
  await apiJson(`/sessions/${sessionId}/runs/current/steer`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export async function abortAgentRun(sessionId: string): Promise<void> {
  await apiJson(`/sessions/${sessionId}/runs/current/abort`, { method: "POST" });
}

async function streamAgentResponse(
  path: string,
  init: RequestInit,
  onEvent: (event: AgentEvent) => void,
  onConnected?: () => void,
): Promise<void> {
  const response = await fetch(apiPath(path), init);
  if (!response.ok || !response.body) {
    throw new Error((await response.text()) || "Agent 流连接失败");
  }
  onConnected?.();

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal = false;
  const receive = (event: AgentEvent) => {
    terminal ||= event.type === "agent_end";
    onEvent(event);
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) emitFrame(frame, receive);
    }
    buffer += decoder.decode();
    if (buffer.trim()) emitFrame(buffer, receive);
    if (!terminal) throw new Error("运行连接提前结束，请检查会话后继续");
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

function emitFrame(frame: string, onEvent: (event: AgentEvent) => void): void {
  const data = frame
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => line.slice(6))
    .join("\n");
  if (data) onEvent(JSON.parse(data) as AgentEvent);
}


export async function fetchSubagents(sessionId: string): Promise<SubagentSnapshot[]> {
  const result = await apiJson<{ subagents: SubagentSnapshot[] }>(`/sessions/${sessionId}/subagents`);
  return result.subagents;
}
