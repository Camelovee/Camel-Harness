import type { Message, RunSummary, ToolExecutionStatus } from "../../shared/model";

export type RunToolActivity = {
  key: string;
  callId: string;
  name: string;
  arguments: Record<string, unknown>;
  timestamp: string;
  status: ToolExecutionStatus | "pending";
  result: Message | null;
  path: string | null;
};

export const runStatusLabels: Record<RunSummary["status"], string> = {
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export const stopReasonLabels: Record<string, string> = {
  model_completed: "模型完成",
  model_length: "输出达到长度上限",
  max_turns: "达到轮数上限",
  provider_failed: "模型服务失败",
  session_failed: "会话保存失败",
  context_failed: "上下文处理失败",
  resource_failed: "资源加载失败",
  tool_failed: "工具执行失败",
  internal_error: "内部错误",
  cancelled: "已取消",
  timeout: "运行超时",
};

export function messagesInRun(messages: Message[], run: RunSummary): Message[] {
  return messages.filter(
    (message) =>
      message.timestamp !== undefined &&
      message.timestamp >= run.started_at &&
      message.timestamp <= run.finished_at,
  );
}

export function messagesAfter(messages: Message[], finishedAt: string | undefined): Message[] {
  return finishedAt ? messages.filter((message) => !message.timestamp || message.timestamp > finishedAt) : messages;
}

export function projectRunTools(messages: Message[]): RunToolActivity[] {
  const items: RunToolActivity[] = [];
  const byCallId = new Map<string, RunToolActivity>();
  for (const message of messages) {
    if (message.role === "assistant") {
      for (const block of message.content) {
        if (block.type !== "tool_use" || !block.call_id) continue;
        const path = block.arguments?.path;
        const item: RunToolActivity = {
          key: `${block.call_id}-${items.length}`,
          callId: block.call_id,
          name: block.name ?? "工具",
          arguments: block.arguments ?? {},
          timestamp: message.timestamp ?? "",
          status: "pending",
          result: null,
          path: typeof path === "string" ? path : null,
        };
        items.push(item);
        byCallId.set(block.call_id, item);
      }
      continue;
    }
    if (message.role !== "toolResult") continue;
    const block = message.content[0];
    const item = block?.call_id ? byCallId.get(block.call_id) : undefined;
    if (!item) continue;
    item.result = message;
    item.status = block.details?.execution_status ?? (block.is_error ? "failed" : "succeeded");
  }
  return items;
}

export function formatDuration(run: RunSummary): string {
  const seconds = Math.max(0, Math.round((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000));
  if (!Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, "0")} 秒`;
}

export function redactHome(path: string): string {
  return path
    .replace(/^\/(?:Users|home)\/[^/]+\//, "~/")
    .replace(/^[A-Za-z]:\\Users\\[^\\]+\\/, "~\\");
}

export function buildRunReport(run: RunSummary, messages: Message[]): string {
  const lines = [
    "## 运行摘要",
    `- 状态：${runStatusLabels[run.status]}${run.stop_reason ? `（${stopReasonLabels[run.stop_reason] ?? run.stop_reason}）` : ""}`,
    `- 模型：${[run.provider, run.model].filter(Boolean).join(" / ") || "未知"}`,
    `- 时长：${formatDuration(run)}`,
    `- 用量：输入 ${run.input_tokens?.toLocaleString() ?? "未知"} · 输出 ${run.output_tokens?.toLocaleString() ?? "未知"} · 合计 ${run.total_tokens.toLocaleString()}`,
    `- 工具失败：${run.tool_failures}`,
  ];
  if (run.files.length) {
    lines.push("", "### 变更文件", ...run.files.map((path) => `- \`${redactHome(path)}\``));
  }
  if (run.approvals.length) {
    lines.push("", "### 审批");
    for (const approval of run.approvals) {
      lines.push(
        `- ${approval.approved ? "允许" : "拒绝"} \`${approval.tool_name}\`（${new Date(approval.decided_at).toLocaleString()}）`,
      );
    }
  }
  if (run.subagent_ids.length) {
    lines.push("", `### 子任务`, `- ${run.subagent_ids.length} 个`);
  }
  const conclusion = [...messages]
    .reverse()
    .find((message) => message.role === "assistant" && message.content.some((block) => block.type === "text" && block.text));
  const text = conclusion?.content.filter((block) => block.type === "text").map((block) => block.text).join("");
  if (text) lines.push("", "### 结论", text);
  return lines.join("\n");
}
