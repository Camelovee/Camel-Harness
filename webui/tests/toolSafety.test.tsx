// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { fetchPendingApprovals } from "../src/features/agent/api";
import { MessageList } from "../src/features/agent/MessageList";
import { projectAgentEvent } from "../src/features/agent/streamProjection";
import type { AgentEvent, Message, ToolExecutionStatus } from "../src/shared/model";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function showMessages(messages: Message[]) {
  return render(<MessageList messages={messages} activeSession={undefined} activeWorkspace={null}
    running={false} streamAssistantId={null} resolvingApprovalId={null}
    onResolveApproval={vi.fn()} onStart={vi.fn()} />);
}

function projectMessages(events: AgentEvent[]): Message[] {
  let messages: Message[] = [];
  const context: Parameters<typeof projectAgentEvent>[2] = {
    activeId: "a", lastSequence: { current: 0 }, streamAssistantId: { current: null },
    setMessages: (update) => { messages = typeof update === "function" ? update(messages) : update; },
    setStreamAssistantId: vi.fn(), setUsage: vi.fn(), setApprovals: vi.fn(),
    onError: vi.fn(), onStatus: vi.fn(),
  };
  const assistantIds = new Map<string, string>();
  events.forEach((event) => projectAgentEvent(event, assistantIds, context));
  return messages;
}

it("Shell 实时审批与刷新待办均显示完整运行上下文", async () => {
  const executionContext = { working_directory: "/workspace/中文目录", command: `echo first\n${"echo complete-command\n".repeat(100)}echo last` };
  const base = { request_id: "request", tool_name: "run_shell", arguments: { command: "原始命令" },
    reason: "需要确认", execution_context: executionContext };
  const messages = projectMessages([{
    type: "approval_requested", session_id: "a", run_id: "r", turn_id: "t", sequence: 1,
    request_id: base.request_id, call_id: "call", name: base.tool_name,
    arguments: base.arguments, reason: base.reason, risk: "execute", capabilities: ["process.execute"],
    execution_context: executionContext,
  }]);
  expect(messages[0].approval?.execution_context).toEqual(executionContext);
  const view = showMessages(messages);
  expect(screen.getByText(executionContext.working_directory)).toBeTruthy();
  expect(screen.getByText(/echo first/).textContent).toBe(executionContext.command);
  view.unmount();

  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ approvals: [base] }))));
  const approvals = await fetchPendingApprovals();
  expect(approvals[0].execution_context).toEqual(executionContext);
  showMessages([{ id: "refresh", role: "approval", content: [], approval: approvals[0] }]);
  expect(screen.getByText(executionContext.working_directory)).toBeTruthy();
  expect(screen.getByText(/echo first/).textContent).toBe(executionContext.command);
});

it.each<[ToolExecutionStatus, string]>([
  ["not_executed", "未执行"], ["succeeded", "已完成"], ["failed", "执行失败"], ["unknown", "结果未知"],
])("工具结果保留状态 %s 并显示对应标签", (status, label) => {
  const messages = projectMessages([{
    type: "message_end", session_id: "a", run_id: "r", turn_id: "t", sequence: 1, message_id: "result",
    message: { role: "toolResult", tool_call_id: "call", tool_name: "run_shell", content: "核验后再决定是否重试",
      is_error: status !== "succeeded", details: { execution_status: status } },
  }]);
  expect(messages[0].content[0].details?.execution_status).toBe(status);
  showMessages(messages);
  expect(screen.getByText(label)).toBeTruthy();
  expect(screen.getByText("核验后再决定是否重试")).toBeTruthy();
});

it("旧会话无状态时保留原标签", () => {
  showMessages([{ id: "legacy", role: "toolResult", content: [{ type: "tool_result", name: "read_file", content: "旧结果", is_error: true }] }]);
  expect(screen.getByText("失败")).toBeTruthy();
});
