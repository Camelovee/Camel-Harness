// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { fetchConversation } from "../src/features/agent/api";
import { MessageList } from "../src/features/agent/MessageList";
import { childView } from "../src/features/agent/subagentProjection";
import type { Message, SubagentView, WireMessage } from "../src/shared/model";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function child(sessionId: string, callId: string, task: string): SubagentView {
  return childView({
    session_id: sessionId, parent_session_id: "parent", parent_tool_call_id: callId,
    agent_type: "general", task, status: "running", phase: "正在执行", turn: 1, task_outcome: null,
  });
}

function conversation(messages: Message[], subagents: SubagentView[], onOpenSubagent = vi.fn()) {
  return <MessageList
    activeSession={{ session_id: "parent", name: "父会话", workspace: "/workspace", updated_at: "",
      message_count: messages.length, archived: false, model_id: null }}
    activeWorkspace="/workspace" messages={messages} subagents={subagents}
    running={subagents.some((view) => view.status === "running")} streamAssistantId={null}
    resolvingApprovalId={null} onResolveApproval={vi.fn()} onStart={vi.fn()}
    onOpenSubagent={onOpenSubagent}
  />;
}

function expectInOrder(elements: HTMLElement[]): void {
  for (let index = 1; index < elements.length; index++) {
    expect(elements[index - 1].compareDocumentPosition(elements[index]) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  }
}

it("历史子任务按父消息的委派顺序显示，不受子任务接口返回顺序影响", async () => {
  const history: WireMessage[] = [
    { role: "user", content: "检查项目" },
    { role: "assistant", stop_reason: "tool_use", content: [
      { type: "text", text: "先检查前端" },
      { type: "tool_use", name: "delegate_task", call_id: "first-call", arguments: {} },
    ] },
    { role: "toolResult", tool_name: "delegate_task", tool_call_id: "first-call", content: "前端检查结果", is_error: false },
    { role: "assistant", stop_reason: "tool_use", content: [
      { type: "text", text: "接着检查后端" },
      { type: "tool_use", name: "delegate_task", call_id: "second-call", arguments: {} },
    ] },
    { role: "toolResult", tool_name: "delegate_task", tool_call_id: "second-call", content: "后端检查结果", is_error: false },
    { role: "assistant", stop_reason: "stop", content: [{ type: "text", text: "检查完成" }] },
  ];
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ messages: history }))));
  const frontendChild = child("frontend-child", "first-call", "分析前端任务");
  const backendChild = child("backend-child", "second-call", "分析后端任务");
  render(conversation(await fetchConversation("parent"), [backendChild, frontendChild]));

  expectInOrder([
    screen.getByText("检查项目"), screen.getByText("先检查前端"),
    screen.getByRole("button", { name: /分析前端任务/ }), screen.getByText("前端检查结果"),
    screen.getByText("接着检查后端"), screen.getByRole("button", { name: /分析后端任务/ }),
    screen.getByText("后端检查结果"), screen.getByText("检查完成"),
  ]);
});

it("同一父消息中的多个子任务按工具调用顺序显示，状态更新和返回结果不移动或重复卡片", () => {
  const firstChild = child("first-child", "first-call", "第一个委派任务");
  const secondChild = child("second-child", "second-call", "第二个委派任务");
  const messages: Message[] = [
    { id: "user", role: "user", content: [{ type: "text", text: "开始处理" }] },
    { id: "assistant", role: "assistant", content: [
      { type: "text", text: "依次委派两个任务" },
      { type: "tool_use", name: "delegate_task", call_id: "first-call" },
      { type: "tool_use", name: "delegate_task", call_id: "second-call" },
    ] },
  ];
  const openSubagent = vi.fn();
  const view = render(conversation(messages, [secondChild, firstChild], openSubagent));
  const firstCard = screen.getByRole("button", { name: /第一个委派任务/ });
  const secondCard = screen.getByRole("button", { name: /第二个委派任务/ });
  expectInOrder([screen.getByText("依次委派两个任务"), firstCard, secondCard]);
  fireEvent.click(secondCard);
  expect(openSubagent).toHaveBeenCalledWith("second-child");

  view.rerender(conversation([
    ...messages,
    { id: "result", role: "toolResult", content: [{ type: "tool_result", name: "delegate_task", call_id: "first-call", content: "第一个任务的返回结果" }] },
    { id: "next-user", role: "user", content: [{ type: "text", text: "继续下一步" }] },
  ], [
    { ...firstChild, status: "completed", task_outcome: { status: "completed", stop_reason: "model_completed", error: null, finished_at: "" } },
    { ...secondChild, status: "waiting_approval", approvals: [{ request_id: "approval", tool_name: "run_shell", status: "pending" }] },
  ], openSubagent));
  expect(screen.getByRole("button", { name: /第一个委派任务/ })).toBe(firstCard);
  expect(screen.getByRole("button", { name: /第二个委派任务/ })).toBe(secondCard);
  expect(screen.getAllByRole("button", { name: /委派任务.*查看过程/ })).toHaveLength(2);
  expect(secondCard.textContent).toContain("1 项待审批");
  expectInOrder([firstCard, secondCard, screen.getByText("第一个任务的返回结果"), screen.getByText("继续下一步")]);
});

it("子任务快照先于父消息加载时等待对应调用，不把其他分支的卡片放到顶部", () => {
  const currentChild = child("current-child", "current-call", "当前分支任务");
  const otherChild = child("other-child", "other-call", "其他分支任务");
  const messages: Message[] = [{ id: "user", role: "user", content: [{ type: "text", text: "当前请求" }] }];
  const view = render(conversation(messages, [otherChild, currentChild]));
  expect(screen.queryByRole("button", { name: /当前分支任务/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /其他分支任务/ })).toBeNull();
  view.rerender(conversation([...messages, { id: "assistant", role: "assistant", content: [
    { type: "text", text: "执行当前委派" },
    { type: "tool_use", name: "delegate_task", call_id: "current-call" },
  ] }], [currentChild, otherChild]));
  expectInOrder([screen.getByText("当前请求"), screen.getByText("执行当前委派"), screen.getByRole("button", { name: /当前分支任务/ })]);
  expect(screen.queryByRole("button", { name: /其他分支任务/ })).toBeNull();
});
