import { afterEach, expect, it, vi } from "vitest";
import { prepareExit } from "../src/exit";

const connection = { origin: "http://127.0.0.1:12345", token: "private-token" };
afterEach(() => vi.unstubAllGlobals());

it("没有任务时直接退出，运行中确认后退出", async () => {
  const request = vi.fn(async () => new Response('{"active_runs":0}'));
  vi.stubGlobal("fetch", request);
  const confirm = vi.fn(async () => true);
  expect(await prepareExit(connection, confirm)).toBe(true);
  expect(confirm).not.toHaveBeenCalled();
  request.mockImplementation(async () => new Response('{"active_runs":2}'));
  expect(await prepareExit(connection, confirm)).toBe(true);
  expect(confirm).toHaveBeenCalledWith(2);
  expect(request).toHaveBeenLastCalledWith(`${connection.origin}/api/desktop/exit`, expect.objectContaining({
    method: "POST", headers: { "X-Camel-Desktop-Token": connection.token },
  }));
});

it("取消退出会解除任务入口暂停，不发送关闭操作", async () => {
  const request = vi.fn().mockResolvedValueOnce(new Response('{"active_runs":1}'))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", request);
  expect(await prepareExit(connection, async () => false)).toBe(false);
  expect(request.mock.calls.map((call) => call[1].method)).toEqual(["POST", "DELETE"]);
});

it("无法查询状态时必须确认，取消后恢复失败必须报告", async () => {
  const request = vi.fn(async () => new Response("{}", { status: 503 }));
  vi.stubGlobal("fetch", request);
  const confirm = vi.fn(async () => true);
  expect(await prepareExit(connection, confirm)).toBe(true);
  expect(confirm).toHaveBeenCalledWith(null);
  await expect(prepareExit(connection, async () => false)).rejects.toThrow("无法恢复任务入口");
});
