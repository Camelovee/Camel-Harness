// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "../src/app/App";
import type { AgentEvent, WireMessage } from "../src/shared/model";

let channels: Map<string, { emit: (body: object) => void; close: () => void }>;
let history: Map<string, WireMessage[]>;
let requests: string[];

beforeEach(() => {
  channels = new Map();
  history = new Map();
  requests = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push(url);
    const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
    if (url === "/api/settings") return json({
      models: [{ id: "model", name: "测试模型", model: "fake", provider: "anthropic", configured: true }],
      default_model_id: "model",
    });
    if (url === "/api/workspaces") return json({ workspaces: [{
      workspace: "/workspace", updated_at: "", sessions: ["a", "b"].map((id) => ({
        session_id: id, name: `会话 ${id.toUpperCase()}`, workspace: "/workspace",
        updated_at: "", message_count: history.get(id)?.length ?? 0, archived: false, model_id: "model",
      })),
    }] });
    if (url === "/api/workspaces/default") return json({ workspace: "/home/test" });
    if (url === "/api/approvals") return json({ approvals: [] });
    const match = url.match(/^\/api\/sessions\/([^/]+)\/(.+)$/);
    if (!match) throw new Error(`测试中未配置的接口：${url}`);
    const [, id, action] = match;
    if (action === "messages") return json({ messages: history.get(id) ?? [] });
    if (action === "runs") {
      history.set(id, [{ role: "user", content: JSON.parse(String(init!.body)).prompt }]);
      let sequence = 0;
      const body = new ReadableStream<Uint8Array>({ start(controller) {
        channels.set(id, {
          emit: (event) => {
            const wire = { session_id: id, run_id: `run-${id}`, turn_id: "turn", sequence: ++sequence, ...event } as AgentEvent;
            controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(wire)}\n\n`));
          },
          close: () => controller.close(),
        });
        channels.get(id)!.emit({ type: "agent_start" });
        channels.get(id)!.emit({ type: "message_end", message_id: "user", message: history.get(id)![0] });
      }, cancel() {} });
      return new Response(body);
    }
    if (action === "runs/current/abort") {
      channels.get(id)!.emit({ type: "agent_end", status: "cancelled", total_tokens: 0, error: null });
      channels.get(id)!.close();
      return json({ accepted: true });
    }
    throw new Error(`测试中未配置的接口：${url}`);
  }));
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function openWorkbench() {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "打开工作区 workspace" }));
  await waitFor(() => expect(document.querySelector(".main-header-title")!.textContent).toBe("会话 A"));
}

async function send(prompt: string) {
  fireEvent.change(screen.getByRole("textbox"), { target: { value: prompt } });
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", code: "Enter" });
  await waitFor(() => expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(""));
}

it("实际工作台可在 A 运行时启动 B，切回查看实时输出，暂停 A 后 B 继续", async () => {
  await openWorkbench();
  await send("任务 A");
  expect(screen.getByRole("button", { name: "＋ 新对话" }).hasAttribute("disabled")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "打开会话 会话 B" }));
  await send("任务 B");
  await act(async () => {
    channels.get("a")!.emit({ type: "message_update", message_id: "assistant", assistant_event: { type: "text_delta", text: "A 实时内容" } });
    channels.get("b")!.emit({ type: "message_update", message_id: "assistant", assistant_event: { type: "text_delta", text: "B 实时内容" } });
    channels.get("a")!.emit({ type: "message_update", message_id: "assistant", assistant_event: { type: "usage_updated", usage: { input_tokens: 100, output_tokens: null, cache_read_tokens: null, cache_write_tokens: null, reasoning_tokens: null } } });
  });
  expect(screen.getByText("B 实时内容")).toBeTruthy();
  expect(screen.queryByText("A 实时内容")).toBeNull();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "B 的草稿" } });
  fireEvent.click(screen.getByRole("button", { name: "打开会话 会话 A" }));
  expect(screen.getByText("A 实时内容")).toBeTruthy();
  expect(document.querySelector(".usage-summary")!.textContent).toContain("100");
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "暂停当前 Run" }));
  await waitFor(() => expect(screen.getByText("已暂停")).toBeTruthy());
  expect(requests.filter((url) => url.endsWith("/abort"))).toEqual(["/api/sessions/a/runs/current/abort"]);
  fireEvent.click(screen.getByRole("button", { name: "打开会话 会话 B" }));
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("B 的草稿");
  expect(screen.getByText("B 实时内容")).toBeTruthy();
  expect(screen.getByRole("button", { name: "暂停当前 Run" })).toBeTruthy();
});

it("组合输入 Enter 只确认候选词，不创建运行；Shift Enter 保留换行", async () => {
  await openWorkbench();
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "输入中的中文" } });
  fireEvent.compositionStart(input);
  fireEvent.keyDown(input, { key: "Enter", keyCode: 13, isComposing: true });
  fireEvent.compositionEnd(input);
  fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
  expect(channels.size).toBe(0);
  expect((input as HTMLTextAreaElement).value).toBe("输入中的中文");
});
