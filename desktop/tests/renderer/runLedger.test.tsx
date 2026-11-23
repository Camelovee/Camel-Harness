// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { RunLedgerPanel } from "../../renderer/src/features/agent/RunLedgerPanel";
import type { Message, ModelProfile, RunSummary } from "../../renderer/src/shared/model";

afterEach(cleanup);

const model: ModelProfile = {
  id: "model",
  name: "测试模型",
  model: "claude-test",
  provider: "anthropic",
  configured: true,
  context_window_tokens: 128000,
  max_output_tokens: 8192,
  base_url: null,
  has_api_key: true,
  thinking_mode: "provider_default",
};

const run: RunSummary = {
  run_id: "run-1",
  started_at: "2026-11-23T10:00:00+00:00",
  finished_at: "2026-11-23T10:00:10+00:00",
  status: "completed",
  stop_reason: "model_completed",
  provider: "anthropic",
  model: "claude-test",
  total_tokens: 42,
  input_tokens: 30,
  output_tokens: 12,
  files: ["/Users/alice/project/src/app.py"],
  approvals: [
    {
      request_id: "approval-1",
      tool_name: "edit_file",
      approved: true,
      decided_at: "2026-11-23T10:00:05+00:00",
    },
  ],
  subagent_ids: [],
  tool_failures: 0,
};

const messages: Message[] = [
  {
    id: "user",
    role: "user",
    content: [{ type: "text", text: "修改 app.py" }],
    timestamp: "2026-11-23T10:00:00.100000+00:00",
  },
  {
    id: "assistant",
    role: "assistant",
    content: [
      { type: "tool_use", call_id: "call-1", name: "edit_file", arguments: { path: "/Users/alice/project/src/app.py" } },
      { type: "text", text: "已修改 app.py" },
    ],
    timestamp: "2026-11-23T10:00:04+00:00",
  },
  {
    id: "result",
    role: "toolResult",
    content: [{
      type: "tool_result",
      call_id: "call-1",
      name: "edit_file",
      content: "已编辑 src/app.py（1 个片段）\n@@ -1 +1 @@\n-a\n+b",
      details: { execution_status: "succeeded" },
    }],
    timestamp: "2026-11-23T10:00:05.500000+00:00",
  },
];

function panel(runs: RunSummary[] = [run], selectedRunId: string | null = null) {
  return (
    <RunLedgerPanel
      messages={messages}
      approvals={[]}
      subagents={[]}
      running={false}
      state="idle"
      phase="准备就绪"
      usage={{ input: 30, output: 12, runTotal: 42 }}
      model={model}
      workspace="/Users/alice/project"
      runs={runs}
      selectedRunId={selectedRunId}
      onSelectRun={vi.fn()}
      onOpenTool={vi.fn()}
      onOpenSubagent={vi.fn()}
      onClose={vi.fn()}
      onError={vi.fn()}
    />
  );
}

it("历史运行展示结案摘要、审批、文件与工具结果 diff", () => {
  render(panel());

  const ledger = screen.getByRole("complementary", { name: "运行账本" });
  expect(ledger).toBeTruthy();
  expect(screen.getByText("已完成")).toBeTruthy();
  expect(screen.getByText("模型完成")).toBeTruthy();
  expect(screen.getByText("1 允许 / 0 拒绝")).toBeTruthy();
  expect(screen.getByText("~/project/src/app.py")).toBeTruthy();
  expect(screen.getByText("-a")).toBeTruthy();
  expect(screen.getByText("+b")).toBeTruthy();
});

it("结案报告复制为 Markdown，并把主目录替换成 ~", async () => {
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
  render(panel());

  fireEvent.click(screen.getByRole("button", { name: "复制报告" }));

  await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
  const report = writeText.mock.calls[0][0] as string;
  expect(report).toContain("## 运行摘要");
  expect(report).toContain("~/project/src/app.py");
  expect(report).toContain("### 结论");
  expect(report).toContain("已修改 app.py");
  expect(report).not.toContain("/Users/alice");
});
