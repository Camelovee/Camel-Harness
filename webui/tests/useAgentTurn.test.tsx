// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";

import type { AgentEvent, Message, Session } from "../src/shared/model";
import * as api from "../src/features/agent/api";
import { useAgentTurn } from "../src/features/agent/useAgentTurn";

vi.mock("../src/features/agent/api", () => ({
  fetchSubagents: vi.fn(),
  abortAgentRun: vi.fn(), fetchConversation: vi.fn(), fetchPendingApprovals: vi.fn(),
  steerAgentRun: vi.fn(), streamAgentRun: vi.fn(), streamAgentResume: vi.fn(), submitApproval: vi.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

type Stream = {
  emit: (event: AgentEvent) => void;
  finish: () => void;
  fail: (error: Error) => void;
  signal: AbortSignal;
};
let streams: Map<string, Stream>;
let histories: Map<string, Message[]>;
const refresh = vi.fn<() => Promise<void>>();
const activate = vi.fn();
const create = vi.fn<() => Promise<Session>>();

function harness(id: string | null = "a") {
  return renderHook(({ id }: { id: string | null }) => useAgentTurn({
    activeId: id, activeWorkspace: "/workspace", createDefaultChatSession: create,
    activateSession: activate, refreshWorkspaces: refresh,
  }), { initialProps: { id } });
}

function event(id: string, sequence: number, body: object): AgentEvent {
  return { session_id: id, run_id: `run-${id}`, turn_id: "turn-1", sequence, ...body } as AgentEvent;
}

function output(id: string, sequence: number, text: string) {
  streams.get(id)!.emit(event(id, sequence, {
    type: "message_update", message_id: "assistant",
    assistant_event: { type: "text_delta", text },
  }));
}

function complete(id: string, tokens: number) {
  streams.get(id)!.emit(event(id, 100, {
    type: "agent_end", status: "completed", stop_reason: "completed", total_tokens: tokens, error: null,
  }));
  streams.get(id)!.finish();
}

beforeEach(() => {
  vi.resetAllMocks();
  streams = new Map();
  histories = new Map();
  refresh.mockResolvedValue();
  vi.mocked(api.fetchConversation).mockImplementation(async (id) => histories.get(id) ?? []);
  vi.mocked(api.fetchPendingApprovals).mockResolvedValue([]);
  vi.mocked(api.fetchSubagents).mockResolvedValue([]);
  const open: typeof api.streamAgentRun = (id, _request, emit, options = {}) => {
    const done = deferred<void>();
    const signal = options.signal!;
    signal.addEventListener("abort", () => done.reject(new DOMException("取消连接", "AbortError")), { once: true });
    streams.set(id, { emit, signal, finish: () => done.resolve(), fail: done.reject });
    emit(event(id, 1, { type: "agent_start" }));
    options.onConnected?.();
    return done.promise;
  };
  vi.mocked(api.streamAgentRun).mockImplementation(open);
  vi.mocked(api.streamAgentResume).mockImplementation((id, request, emit, options) =>
    open(id, { ...request, prompt: "" }, emit, options));
});

afterEach(cleanup);

describe("按会话隔离并行运行", () => {
  it("StrictMode 下只初始化一次审批查询，焦点、可见性、会话切换和运行不触发重查", async () => {
    const { result, rerender } = renderHook(({ id }: { id: string }) => useAgentTurn({
      activeId: id, activeWorkspace: "/workspace", createDefaultChatSession: create,
      activateSession: activate, refreshWorkspaces: refresh,
    }), {
      initialProps: { id: "a" },
      wrapper: ({ children }) => <StrictMode>{children}</StrictMode>,
    });
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(1);
    act(() => {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
    });
    rerender({ id: "b" });
    await act(async () => { await result.current.sendPrompt("B", "default", false, "model"); });
    act(() => streams.get("b")!.emit(event("b", 2, {
      type: "approval_requested", request_id: "approval-b", call_id: "call-b", name: "run_shell",
      capabilities: [], risk: "high", arguments: {}, reason: "确认执行",
    })));
    expect(result.current.approvals[0].request_id).toBe("approval-b");
    await act(async () => { await result.current.resolveApproval("approval-b", true); });
    expect(result.current.approvals).toEqual([]);
    await act(async () => { complete("b", 0); });
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(1);
  });

  it("切换后保持两条流，交错文本与 Token 不串会话，后台完成不抢回选择", async () => {
    const { result, rerender } = harness();
    await act(async () => { expect(await result.current.sendPrompt("任务 A", "default", false, "model-a")).toBe(true); });
    rerender({ id: "b" });
    await act(async () => { expect(await result.current.sendPrompt("任务 B", "plan", true, "model-b")).toBe(true); });
    act(() => {
      output("a", 2, "A 的回答");
      output("b", 2, "B 的回答");
      streams.get("a")!.emit(event("a", 3, { type: "message_update", message_id: "assistant", assistant_event: { type: "usage_updated", usage: { input_tokens: 11, output_tokens: null, cache_read_tokens: null, cache_write_tokens: null, reasoning_tokens: null } } }));
      streams.get("b")!.emit(event("b", 3, { type: "message_update", message_id: "assistant", assistant_event: { type: "usage_updated", usage: { input_tokens: 22, output_tokens: null, cache_read_tokens: null, cache_write_tokens: null, reasoning_tokens: null } } }));
    });
    expect(result.current.messages[0].content[0].text).toBe("B 的回答");
    expect(result.current.usage.input).toBe(22);
    expect(streams.get("a")!.signal.aborted).toBe(false);
    histories.set("a", [{ id: "stored-a", role: "assistant", content: [{ type: "text", text: "A 完成" }] }]);
    await act(async () => { complete("a", 31); });
    expect(result.current.running).toBe(true);
    expect(result.current.messages[0].content[0].text).toBe("B 的回答");
    expect(result.current.indicators.a).toMatchObject({ running: false, unread: true });
    expect(refresh).toHaveBeenCalledWith();
    expect(activate).not.toHaveBeenCalled();
    rerender({ id: "a" });
    expect(result.current.messages[0].content[0].text).toBe("A 完成");
    expect(result.current.usage).toMatchObject({ input: 11, runTotal: 31 });
    expect(result.current.indicators.a.unread).toBe(false);
  });

  it("追加输入和暂停只针对选中会话，同会话不会重复启动", async () => {
    const { result, rerender } = harness();
    await act(async () => { await result.current.sendPrompt("A", "default", false, "model"); });
    rerender({ id: "b" });
    await act(async () => { await result.current.sendPrompt("B", "default", false, "model"); });
    await act(async () => { await result.current.sendPrompt("追加 B", "default", false, "model"); await result.current.abortRun(); });
    expect(api.steerAgentRun).toHaveBeenCalledExactlyOnceWith("b", "追加 B");
    expect(api.abortAgentRun).toHaveBeenCalledExactlyOnceWith("b");
    expect(api.streamAgentRun).toHaveBeenCalledTimes(2);
    expect(result.current.indicators.a.running).toBe(true);
    expect(streams.get("a")!.signal.aborted).toBe(false);
  });

  it("暂停响应晚于终态时保留取消状态，重复旧事件不能覆盖终态", async () => {
    const stopped = deferred<void>();
    vi.mocked(api.abortAgentRun).mockReturnValueOnce(stopped.promise);
    const { result } = harness();
    await act(async () => { await result.current.sendPrompt("A", "default", false, "model"); });
    let abort!: Promise<void>;
    act(() => { abort = result.current.abortRun(); });
    await act(async () => {
      streams.get("a")!.emit(event("a", 100, { type: "agent_end", status: "cancelled", total_tokens: 0, error: null }));
      streams.get("a")!.finish();
    });
    await act(async () => { stopped.resolve(); await abort; });
    expect(result.current.status).toBe("运行已取消");
    act(() => streams.get("a")!.emit(event("a", 99, { type: "agent_end", status: "failed", total_tokens: 1, error: "迟到事件" })));
    expect(result.current.endStatus).toBe("cancelled");
    expect(result.current.error).toBeNull();
  });

  it("后台审批有侧栏标识，仅所属会话可以处理，迟到查询不会恢复已处理卡片", async () => {
    const pending = deferred<Awaited<ReturnType<typeof api.fetchPendingApprovals>>>();
    vi.mocked(api.fetchPendingApprovals).mockReturnValueOnce(pending.promise);
    const { result, rerender } = harness();
    await act(async () => { await result.current.sendPrompt("A", "default", false, "model"); });
    rerender({ id: "b" });
    act(() => streams.get("a")!.emit(event("a", 2, {
      type: "approval_requested", request_id: "approval-a", call_id: "call-a", name: "run_shell",
      capabilities: [], risk: "high", arguments: {}, reason: "确认执行",
    })));
    expect(result.current.indicators.a.approvalCount).toBe(1);
    expect(result.current.messages).toEqual([]);
    await act(async () => { await result.current.resolveApproval("approval-a", true); });
    expect(api.submitApproval).not.toHaveBeenCalled();
    rerender({ id: "a" });
    await act(async () => { await result.current.resolveApproval("approval-a", true); });
    await act(async () => { pending.resolve([{ request_id: "approval-a", session_id: "a", tool_name: "run_shell", status: "pending" }]); });
    expect(api.submitApproval).toHaveBeenCalledExactlyOnceWith("approval-a", true);
    expect(result.current.messages.filter((message) => message.role === "approval")).toEqual([]);
    expect(result.current.indicators.a.approvalCount).toBe(0);
  });

  it("草稿与权限随会话保存，迟到历史只更新原会话", async () => {
    const delayed = deferred<Message[]>();
    vi.mocked(api.fetchConversation).mockReturnValueOnce(delayed.promise);
    const { result, rerender } = harness();
    act(() => { result.current.setPrompt("A 草稿"); result.current.setPermissionMode("plan"); });
    const clearA = result.current.setPrompt;
    rerender({ id: "b" });
    await waitFor(() => expect(result.current.loaded).toBe(true));
    act(() => result.current.setPrompt("B 草稿"));
    await act(async () => { delayed.resolve([{ id: "history-a", role: "user", content: [{ type: "text", text: "A 历史" }] }]); });
    expect(result.current.messages).toEqual([]);
    expect(result.current.prompt).toBe("B 草稿");
    act(() => clearA(""));
    expect(result.current.prompt).toBe("B 草稿");
    rerender({ id: "a" });
    expect(result.current.permissionMode).toBe("plan");
    expect(result.current.messages[0].content[0].text).toBe("A 历史");
  });

  it("创建新会话期间切走仍能运行新会话，完成创建不会跳走", async () => {
    const deferredSession = deferred<Session>();
    create.mockReturnValue(deferredSession.promise);
    const { result, rerender } = harness(null);
    let accepted!: Promise<boolean>;
    act(() => { accepted = result.current.sendPrompt("新任务", "default", false, "model"); });
    rerender({ id: "b" });
    await act(async () => { await result.current.sendPrompt("B", "default", false, "model"); });
    await act(async () => {
      deferredSession.resolve({ session_id: "new", name: "新会话", workspace: "/workspace", updated_at: "", message_count: 0, archived: false, model_id: null });
      expect(await accepted).toBe(true);
    });
    expect(activate).not.toHaveBeenCalled();
    expect(result.current.indicators.new.running).toBe(true);
    expect(result.current.indicators.b.running).toBe(true);
  });

  it("一个流失败不改变另一会话状态，离开页面关闭全部连接", async () => {
    const { result, rerender, unmount } = harness();
    await act(async () => { await result.current.sendPrompt("A", "default", false, "model"); });
    rerender({ id: "b" });
    await act(async () => { await result.current.sendPrompt("B", "default", false, "model"); });
    await act(async () => { streams.get("a")!.fail(new Error("A 网络失败")); });
    expect(result.current.error).toBeNull();
    expect(result.current.running).toBe(true);
    expect(result.current.indicators.a.failed).toBe(true);
    unmount();
    expect(streams.get("b")!.signal.aborted).toBe(true);
  });

  it("恢复会话使用自己的流，HTTP 拒绝启动时保留失败状态", async () => {
    histories.set("a", [{ id: "pending", role: "user", content: [{ type: "text", text: "待恢复" }] }]);
    const { result, rerender } = harness();
    await waitFor(() => expect(result.current.canResume).toBe(true));
    await act(async () => { await result.current.resumeRun("default", false, "model"); });
    expect(api.streamAgentResume).toHaveBeenCalledOnce();
    rerender({ id: "b" });
    vi.mocked(api.streamAgentRun).mockRejectedValueOnce(new Error("模型未配置"));
    await act(async () => { expect(await result.current.sendPrompt("B", "default", false, "model")).toBe(false); });
    expect(result.current.error).toBe("模型未配置");
    expect(result.current.indicators.a.running).toBe(true);
  });
});

it.each([
  ["model_length", "输出截断"],
  ["max_turns", "Turn 耗尽"],
  ["context_failed", "上下文容量或压缩失败"],
])("按结束原因显示 %s 且保留完整落盘的部分输出", async (reason, label) => {
  const { result } = harness();
  await act(async () => { await result.current.sendPrompt("任务", "default", false, "model"); });
  histories.set("a", [{ id: "assistant-saved", role: "assistant", content: [{ type: "text", text: "已收到的内容" }] }]);
  await act(async () => {
    streams.get("a")!.emit(event("a", 2, {
      type: "message_end", message_id: "saved", message: {
        role: "assistant", content: [{ type: "text", text: "已收到的内容" }], stop_reason: "length",
      },
    }));
    streams.get("a")!.emit(event("a", 3, {
      type: "agent_end", status: "failed", stop_reason: reason, total_tokens: 100, error: "诊断信息",
    }));
    streams.get("a")!.finish();
  });
  expect(result.current.status).toBe(label);
  expect(result.current.error).toBe("诊断信息");
  expect(result.current.messages.some((message) => message.content[0]?.text === "已收到的内容")).toBe(true);
});

function childEvent(parent: string, sequence: number, childSequence: number, body: object): AgentEvent {
  return event(parent, sequence, { type: "subagent_event", call_id: `delegate-${parent}`, agent_type: "general",
    event: { session_id: `child-${parent}`, run_id: `child-run-${parent}`, sequence: childSequence, ...body } });
}

it("子任务完整过程独立投影、审批在父会话处理、子终态不结束父运行", async () => {
  const { result } = harness();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  await act(async () => { await result.current.sendPrompt("委派任务", "default", "low", "model"); });
  act(() => {
    streams.get("a")!.emit(event("a", 2, { type: "message_end", message_id: "parent-message", turn_id: "turn",
      message: { role: "assistant", content: [{ type: "tool_use", call_id: "delegate-a", name: "delegate_task", arguments: { task: "检查文件" } }] } }));
    streams.get("a")!.emit(childEvent("a", 3, 1, { type: "agent_start" }));
    streams.get("a")!.emit(childEvent("a", 4, 2, { type: "message_update", message_id: "child-message", turn_id: "child-turn",
      assistant_event: { type: "reasoning_delta", text: "子任务的独立思考" } }));
    streams.get("a")!.emit(childEvent("a", 5, 3, { type: "message_end", message_id: "child-message", turn_id: "child-turn",
      message: { role: "assistant", content: [{ type: "reasoning", text: "子任务的独立思考" }, { type: "text", text: "准备执行" }] } }));
    streams.get("a")!.emit(childEvent("a", 6, 4, { type: "approval_requested", turn_id: "child-turn",
      request_id: "child-request", call_id: "write", name: "write_file", risk: "write", capabilities: ["filesystem.write"],
      arguments: { path: "a.txt", content: "text" }, reason: "子任务修改文件" }));
  });
  expect(result.current.selectedSubagentId).toBe("child-a");
  expect(result.current.subagents["child-a"].task).toBe("检查文件");
  expect(result.current.subagents["child-a"].status).toBe("waiting_approval");
  expect(result.current.approvals[0].session_id).toBe("child-a");
  expect(result.current.approvals[0].parent_session_id).toBe("a");
  expect(result.current.messages.some((message) => JSON.stringify(message).includes("独立思考"))).toBe(false);
  await act(async () => { await result.current.resolveApproval("child-request", true); });
  expect(api.submitApproval).toHaveBeenCalledWith("child-request", true);
  act(() => {
    streams.get("a")!.emit(childEvent("a", 7, 5, { type: "approval_resolved", turn_id: "child-turn",
      request_id: "child-request", call_id: "write", approved: true, reason: "已批准" }));
  });
  expect(result.current.subagents["child-a"].phase).toContain("执行中");
  act(() => streams.get("a")!.emit(childEvent("a", 8, 6, { type: "agent_end", status: "completed",
    stop_reason: "model_completed", total_tokens: 900, error: null })));
  expect(result.current.running).toBe(true);
  expect(result.current.endStatus).toBeNull();
  expect(result.current.usage.runTotal).toBeNull();
  expect(result.current.subagents["child-a"].status).toBe("completed");
  expect(result.current.subagents["child-a"].approvals).toEqual([]);
  act(() => complete("a", 1000));
  await waitFor(() => expect(result.current.running).toBe(false));
});

it("后台子任务审批只更新所属父会话，不抢占当前会话侧栏", async () => {
  vi.mocked(api.fetchSubagents).mockImplementation(async (id) => id === "a" ? [{
    session_id: "child-a", parent_session_id: "a", parent_tool_call_id: "delegate-a", agent_type: "general",
    task: "后台任务", status: "running", phase: "分析中", turn: 1, task_outcome: null,
  }] : []);
  const { result, rerender } = harness();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  await act(async () => { await result.current.sendPrompt("委派", "default", "low", "model"); });
  rerender({ id: "b" });
  await waitFor(() => expect(result.current.loaded).toBe(true));
  act(() => streams.get("a")!.emit(childEvent("a", 2, 1, { type: "approval_requested", turn_id: "child-turn",
    request_id: "background-request", call_id: "shell", name: "run_shell", risk: "execute", capabilities: [],
    arguments: { command: "echo ok" }, reason: "等待审批" })));
  expect(result.current.selectedSubagentId).toBeNull();
  expect(result.current.indicators.a.approvalCount).toBe(1);
  expect(result.current.indicators.a.unread).toBe(true);
  rerender({ id: "a" });
  await act(async () => { await result.current.openSubagent("child-a"); });
  expect(result.current.selectedSubagentId).toBe("child-a");
  expect(result.current.approvals[0].request_id).toBe("background-request");
  act(() => complete("a", 0));
  await waitFor(() => expect(result.current.running).toBe(false));
});

it("历史侧栏复用已保存消息，关闭与重开不重复加载或启动任务", async () => {
  const outcome = { status: "failed" as const, stop_reason: "max_turns", error: "轮数耗尽", finished_at: "2026-10-27T00:00:00Z" };
  vi.mocked(api.fetchSubagents).mockResolvedValue([{ session_id: "historical-child", parent_session_id: "a", parent_tool_call_id: "delegate",
    agent_type: "explore", task: "检查历史", status: "failed", phase: "已结束", turn: 8, task_outcome: outcome }]);
  histories.set("historical-child", [{ id: "saved", role: "assistant", content: [{ type: "text", text: "已保存的子任务消息" }] }]);
  const { result } = harness();
  await waitFor(() => expect(result.current.subagents["historical-child"]).toBeDefined());
  await act(async () => { await result.current.openSubagent("historical-child"); });
  expect(result.current.subagents["historical-child"].messages[0].content[0].text).toBe("已保存的子任务消息");
  expect(result.current.subagents["historical-child"].task_outcome).toEqual(outcome);
  act(() => result.current.closeSubagent());
  expect(result.current.selectedSubagentId).toBeNull();
  await act(async () => { await result.current.openSubagent("historical-child"); });
  expect(vi.mocked(api.fetchConversation).mock.calls.filter(([id]) => id === "historical-child")).toHaveLength(1);
  expect(api.streamAgentRun).not.toHaveBeenCalled();
});

it("迟到的子任务待审批快照不能覆盖已收到的审批完成事件", async () => {
  const pending = deferred<Awaited<ReturnType<typeof api.fetchPendingApprovals>>>();
  vi.mocked(api.fetchPendingApprovals).mockReturnValue(pending.promise);
  const { result } = harness();
  await waitFor(() => expect(result.current.loaded).toBe(true));
  await act(async () => { await result.current.sendPrompt("委派", "default", "low", "model"); });
  act(() => {
    streams.get("a")!.emit(childEvent("a", 2, 1, { type: "approval_requested", request_id: "stale", call_id: "write",
      name: "write_file", risk: "write", arguments: {}, capabilities: [], reason: "等待", turn_id: "child-turn" }));
    streams.get("a")!.emit(childEvent("a", 3, 2, { type: "approval_resolved", request_id: "stale", call_id: "write",
      approved: true, reason: "已批准", turn_id: "child-turn" }));
  });
  await act(async () => pending.resolve([{ request_id: "stale", session_id: "child-a", parent_session_id: "a",
    parent_tool_call_id: "delegate-a", tool_name: "write_file", status: "pending" }]));
  expect(result.current.approvals).toEqual([]);
  expect(result.current.subagents["child-a"].status).toBe("running");
  act(() => complete("a", 0));
  await waitFor(() => expect(result.current.running).toBe(false));
});

it("刷新恢复审批后，迟到的子任务列表保留审批并自动加载侧栏历史", async () => {
  const snapshots = deferred<Awaited<ReturnType<typeof api.fetchSubagents>>>();
  vi.mocked(api.fetchSubagents).mockReturnValue(snapshots.promise);
  vi.mocked(api.fetchPendingApprovals).mockResolvedValue([{ request_id: "restored", session_id: "restored-child",
    parent_session_id: "a", parent_tool_call_id: "delegate", tool_name: "run_shell", status: "pending" }]);
  histories.set("restored-child", [{ id: "saved", role: "assistant", content: [{ type: "text", text: "刷新前的完整消息" }] }]);
  const { result } = harness();
  await waitFor(() => expect(result.current.subagents["restored-child"]?.loaded).toBe(true));
  expect(result.current.selectedSubagentId).toBe("restored-child");
  await act(async () => snapshots.resolve([{ session_id: "restored-child", parent_session_id: "a", parent_tool_call_id: "delegate",
    agent_type: "general", task: "恢复任务", status: "running", phase: "分析中", turn: 2, task_outcome: null }]));
  expect(result.current.subagents["restored-child"].messages[0].content[0].text).toBe("刷新前的完整消息");
  expect(result.current.subagents["restored-child"].approvals).toHaveLength(1);
  expect(result.current.subagents["restored-child"].status).toBe("waiting_approval");
  expect(result.current.subagents["restored-child"].task).toBe("恢复任务");
});

it("已恢复的终态不能被迟到的待审批快照重新变成等待", async () => {
  const pending = deferred<Awaited<ReturnType<typeof api.fetchPendingApprovals>>>();
  vi.mocked(api.fetchPendingApprovals).mockReturnValue(pending.promise);
  vi.mocked(api.fetchSubagents).mockResolvedValue([{ session_id: "ended-child", parent_session_id: "a", parent_tool_call_id: "delegate",
    agent_type: "general", task: "历史任务", status: "cancelled", phase: "已结束", turn: 2,
    task_outcome: { status: "cancelled", stop_reason: "cancelled", error: "运行已取消", finished_at: "2026-10-27T00:00:00Z" } }]);
  const { result } = harness();
  await waitFor(() => expect(result.current.subagents["ended-child"]?.status).toBe("cancelled"));
  await act(async () => pending.resolve([{ request_id: "outdated", session_id: "ended-child", parent_session_id: "a",
    parent_tool_call_id: "delegate", tool_name: "run_shell", status: "pending" }]));
  expect(result.current.approvals).toEqual([]);
  expect(result.current.subagents["ended-child"].status).toBe("cancelled");
  expect(result.current.selectedSubagentId).toBeNull();
});
