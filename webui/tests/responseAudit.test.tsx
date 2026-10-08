// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { MessageList } from "../src/features/agent/MessageList";
import { projectAgentEvent } from "../src/features/agent/streamProjection";
import type { AgentEvent, Message } from "../src/shared/model";

afterEach(cleanup);

it("实时完整消息保留审计信息和文件引用", () => {
  let messages: Message[] = [];
  const context: Parameters<typeof projectAgentEvent>[2] = {
    activeId: "a", lastSequence: { current: 0 }, streamAssistantId: { current: null },
    setMessages: (update) => { messages = typeof update === "function" ? update(messages) : update; },
    setStreamAssistantId: vi.fn(), setUsage: vi.fn(), setApprovals: vi.fn(),
    onError: vi.fn(), onStatus: vi.fn(),
  };
  const assistantIds = new Map<string, string>();
  const usage = {
    input_tokens: 0, output_tokens: null, total_tokens: null,
    cache_read_tokens: null, cache_write_tokens: null, reasoning_tokens: null,
  };
  const reference = {
    path: "/sessions/a/tool-results/file.txt", mime_type: "text/plain; charset=utf-8",
    size_bytes: 60, original_chars: 20, preview_chars: 5,
  };
  const base = { session_id: "a", run_id: "r", turn_id: "t" };
  const events: AgentEvent[] = [
    { ...base, type: "message_end", sequence: 1, message_id: "answer", message: {
      role: "assistant", content: [{ type: "text", text: "回答" }], stop_reason: "stop",
      provider: "anthropic", model: "requested", response_model: "actual", usage,
    } },
    { ...base, type: "message_end", sequence: 2, message_id: "tool-call", message: {
      role: "toolResult", tool_call_id: "call", tool_name: "read_file", content: "预览",
      is_error: false, details: { content_ref: reference },
    } },
  ];
  events.forEach((event) => projectAgentEvent(event, assistantIds, context));
  expect(messages[0]).toMatchObject({ provider: "anthropic", model: "requested", response_model: "actual", usage });
  expect(messages[1].content[0].details?.content_ref).toEqual(reference);
  render(<MessageList messages={messages} activeSession={undefined} activeWorkspace={null}
    running={false} streamAssistantId={null} resolvingApprovalId={null}
    onResolveApproval={vi.fn()} onStart={vi.fn()} />);
  expect(screen.getByText(/anthropic \/ actual/).textContent).toContain("输入 0");
  expect(screen.getByText(/anthropic \/ actual/).textContent).toContain("输出 未知");
  expect(screen.getByText(/完整结果：/).textContent).toContain(reference.path);
});
