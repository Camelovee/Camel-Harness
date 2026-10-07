import { afterEach, expect, it, vi } from "vitest";

import { streamAgentRun } from "../src/features/agent/api";

afterEach(() => vi.unstubAllGlobals());

it("收到响应即确认发送，必须等唯一终态后才算流完整结束", async () => {
  let close!: () => void;
  const connected = vi.fn();
  const receive = vi.fn();
  const body = new ReadableStream<Uint8Array>({ start(controller) {
    controller.enqueue(new TextEncoder().encode('data: {"type":"agent_start","session_id":"a","run_id":"r","sequence":1}\n\n'));
    close = () => {
      controller.enqueue(new TextEncoder().encode('data: {"type":"agent_end","session_id":"a","run_id":"r","sequence":2,"status":"completed","total_tokens":10}\n\n'));
      controller.close();
    };
  } });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
  const request = streamAgentRun("a", { prompt: "任务", permissionMode: "default", thinking: false, modelId: "model" }, receive, { onConnected: connected });
  await vi.waitFor(() => expect(receive).toHaveBeenCalledOnce());
  expect(connected).toHaveBeenCalledOnce();
  close();
  await request;
  expect(receive).toHaveBeenCalledTimes(2);
});

it("提前断流报告失败，避免把未完成运行显示为正常结束", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('data: {"type":"agent_start","session_id":"a","run_id":"r","sequence":1}\n\n')));
  await expect(streamAgentRun("a", { prompt: "任务", permissionMode: "default", thinking: false, modelId: "model" }, vi.fn())).rejects.toThrow("运行连接提前结束");
});
