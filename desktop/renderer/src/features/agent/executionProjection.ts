import type { Approval, Message, ToolExecutionStatus } from "../../shared/model";

export type ToolActivity = {
  callId: string;
  name: string;
  path: string | null;
  status: ToolExecutionStatus | "pending" | "incomplete";
  result: Message | undefined;
};

export function projectToolActivity(messages: Message[], approvals: Approval[]): ToolActivity[] {
  const results = new Map(messages.filter((message) => message.role === "toolResult")
    .map((message) => [message.content[0]?.call_id, message]));
  const pending = new Set(approvals.filter((approval) => approval.status === "pending").map((approval) => approval.call_id));
  return messages.flatMap((message) => message.role !== "assistant" ? [] : message.content
    .filter((block) => block.type === "tool_use" && block.call_id && block.name)
    .map((block): ToolActivity => {
      const result = results.get(block.call_id);
      const resultBlock = result?.content[0];
      return {
        callId: block.call_id!, name: block.name!,
        path: typeof block.arguments?.path === "string" ? block.arguments.path : null,
        status: resultBlock ? resultBlock.details?.execution_status ?? (resultBlock.is_error ? "failed" : "succeeded")
          : pending.has(block.call_id) ? "pending" : "incomplete",
        result,
      };
    }));
}
