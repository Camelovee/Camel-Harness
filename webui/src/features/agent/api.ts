import { apiJson, apiPath } from "../../shared/api";
import type {
  AgentEvent,
  Approval,
  Message,
  PermissionMode,
  WireMessage,
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
  thinking: boolean;
};

export async function streamAgentRun(
  sessionId: string,
  request: RunRequest,
  onEvent: (event: AgentEvent) => void,
): Promise<void> {
  const response = await fetch(apiPath(`/sessions/${sessionId}/runs`), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      prompt: request.prompt,
      permission_mode: request.permissionMode,
      thinking: request.thinking,
    }),
  });
  if (!response.ok || !response.body) {
    throw new Error((await response.text()) || "Agent 流连接失败");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) emitFrame(frame, onEvent);
  }
  buffer += decoder.decode();
  if (buffer.trim()) emitFrame(buffer, onEvent);
}

function emitFrame(frame: string, onEvent: (event: AgentEvent) => void): void {
  const data = frame
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => line.slice(6))
    .join("\n");
  if (data) onEvent(JSON.parse(data) as AgentEvent);
}
