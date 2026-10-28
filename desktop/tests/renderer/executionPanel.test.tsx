// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ExecutionPanel } from "../../renderer/src/features/agent/ExecutionPanel";
import { projectToolActivity } from "../../renderer/src/features/agent/executionProjection";
import type { Approval, Message, ModelProfile, TokenUsage } from "../../renderer/src/shared/model";

afterEach(cleanup);

const model: ModelProfile = { id: "model", name: "我的模型", model: "local-test", provider: "anthropic", configured: true,
  context_window_tokens: 100000, max_output_tokens: 8192, base_url: null, has_api_key: true, thinking_mode: "provider_default" };
const messages: Message[] = [
  { id: "assistant", role: "assistant", content: [
    { type: "tool_use", call_id: "read", name: "read_file", arguments: { path: "/中文 项目/src/auth.py" } },
    { type: "tool_use", call_id: "write", name: "write_file", arguments: { path: "/中文 项目/tests/auth.py" } },
  ] },
  { id: "write-result", role: "toolResult", content: [{ type: "tool_result", call_id: "write", name: "write_file", content: "写入失败", is_error: true, details: { execution_status: "failed" } }] },
  { id: "read-result", role: "toolResult", content: [{ type: "tool_result", call_id: "read", name: "read_file", content: "请先核验", details: { execution_status: "unknown" } }] },
];

function panel(usage: TokenUsage = { input: 42000, output: 1200, runTotal: 43200 }, onOpenTool = vi.fn()) {
  return <ExecutionPanel messages={messages} approvals={[]} subagents={[]} running={false} state="completed" phase="回答完成"
    usage={usage} model={model} permissionMode="default" effort="low" workspace="/中文 项目"
    onOpenTool={onOpenTool} onOpenSubagent={vi.fn()} onClose={vi.fn()} />;
}

it("文件活动按调用 ID 关联乱序结果，保留失败与未知的执行语义", () => {
  const projected = projectToolActivity(messages, []);
  expect(projected.map((item) => [item.callId, item.status, item.result?.id])).toEqual([
    ["read", "unknown", "read-result"], ["write", "failed", "write-result"],
  ]);
  const onOpenTool = vi.fn();
  render(panel(undefined, onOpenTool));
  const files = screen.getByRole("region", { name: "文件活动" });
  expect(within(files).getByText("读取 · 结果未知")).toBeTruthy();
  expect(within(files).getByText("写入 · 执行失败")).toBeTruthy();
  fireEvent.click(within(files).getByRole("button", { name: /src\/auth.py/ }));
  expect(onOpenTool).toHaveBeenCalledWith(messages[2]);
});

it("没有结果的历史调用和追加指令不被猜测为成功或仍在执行", () => {
  const approval: Approval = { request_id: "approval", call_id: "read", status: "pending", tool_name: "read_file" };
  const projected = projectToolActivity([messages[0], { id: "steer", role: "user", content: [{ type: "text", text: "追加指令" }] }], [approval]);
  expect(projected.map((item) => item.status)).toEqual(["pending", "incomplete"]);
  expect(projected.every((item) => item.result === undefined)).toBe(true);
});

it("输入占比来自真实用量与窗口，输出和本轮总量分别显示", () => {
  render(panel());
  const progress = screen.getByRole("progressbar", { name: "最近请求输入占模型窗口比例" });
  expect(progress.getAttribute("aria-valuenow")).toBe("42");
  const usage = screen.getByRole("region", { name: "Token 用量" });
  expect(within(usage).getByText("1,200")).toBeTruthy();
  expect(within(usage).getByText("43,200")).toBeTruthy();
  expect(within(usage).getByText("最近一次请求的输入占比，不含输出预算。")).toBeTruthy();
});

it("未知用量保持未知，不把它显示成零用量或零占比", () => {
  render(panel({ input: null, output: null, runTotal: null }));
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.queryByText("0%")).toBeNull();
  expect(screen.queryByText("0")).toBeNull();
});
