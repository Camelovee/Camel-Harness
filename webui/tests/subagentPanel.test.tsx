// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SubagentPanel } from "../src/features/agent/SubagentPanel";
import { childView } from "../src/features/agent/subagentProjection";

afterEach(cleanup);

it("侧栏展示完整过程、命令与目录，审批直接使用原始请求 ID", () => {
  const child = childView({ session_id: "child", parent_session_id: "parent", parent_tool_call_id: "delegate",
    agent_type: "general", task: "检查文件", status: "waiting_approval", phase: "等待工具审批", turn: 2, task_outcome: null });
  child.messages = [{ id: "assistant", role: "assistant", content: [
    { type: "reasoning", text: "检查调用边界" }, { type: "text", text: "正在处理文件" },
    { type: "tool_use", call_id: "shell", name: "run_shell", arguments: { command: "echo ok" } },
  ] }];
  child.approvals = [{ request_id: "original-request", session_id: "child", tool_name: "run_shell", status: "pending",
    reason: "请求执行命令", arguments: { command: "echo ok" },
    execution_context: { command: "echo ok", working_directory: "/workspace" } }];
  const resolve = vi.fn();
  const close = vi.fn();
  render(<SubagentPanel child={child} workspace="/workspace" resolvingApprovalId={null} onResolveApproval={resolve} onClose={close} />);
  expect(screen.getByText("正在处理文件")).toBeTruthy();
  expect(screen.getByText("检查调用边界")).toBeTruthy();
  expect(screen.getByText("调用 run_shell")).toBeTruthy();
  expect(screen.getByText("/workspace")).toBeTruthy();
  fireEvent.click(screen.getByText("允许执行"));
  expect(resolve).toHaveBeenCalledWith("original-request", true);
  fireEvent.click(screen.getByLabelText("关闭子任务详情"));
  expect(close).toHaveBeenCalledOnce();
});
