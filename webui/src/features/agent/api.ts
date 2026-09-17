import { apiJson, apiPath } from "../../shared/api";
import type {
  Approval,
  ContentBlock,
  Message,
  PermissionMode,
  StreamEvent,
} from "../../shared/model";

export async function fetchConversation(sessionId: string): Promise<Message[]> {
  const result = await apiJson<{ messages: { role: string; content: ContentBlock[] }[] }>(
    `/sessions/${sessionId}/messages`,
  );
  return result.messages
    .filter((message) => ["user", "assistant", "tool"].includes(message.role))
    .map((message, index) => ({
      id: `${message.role}-${index}-${message.content.length}`,
      role: message.role as Message["role"],
      content: message.content,
    }));
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

type TurnRequest = {
  prompt: string;
  permissionMode: PermissionMode;
  thinking: boolean;
};

export async function streamAgentTurn(
  sessionId: string,
  request: TurnRequest,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch(apiPath(`/sessions/${sessionId}/turns`), {
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

function emitFrame(frame: string, onEvent: (event: StreamEvent) => void): void {
  const data = frame
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => line.slice(6))
    .join("\n");
  if (data) onEvent(JSON.parse(data) as StreamEvent);
}
