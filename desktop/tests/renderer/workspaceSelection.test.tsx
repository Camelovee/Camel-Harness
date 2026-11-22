// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useWorkspaceSessions } from "../../renderer/src/features/workspaces/useWorkspaceSessions";

afterEach(() => { cleanup(); vi.unstubAllGlobals();  });

it("原生目录返回后，等待会话创建完成才解除发送保护", async () => {
  let finishCreation!: (response: Response) => void;
  let created = false;
  const session = { session_id: "new", workspace: "/中文 项目", name: "新对话", message_count: 0, model_id: null, updated_at: "", archived: false };
  window.camelDesktop = { ...window.camelDesktop, selectDirectory: vi.fn(async () => session.workspace), appInfo: vi.fn(), onAction: vi.fn() };
  const request = vi.fn(async (path: string, init?: RequestInit) => {
    const json = (body: unknown) => new Response(JSON.stringify(body));
    if (path === "/api/workspaces/default") return json({ workspace: "/default" });
    if (path === "/api/workspaces") return json({ workspaces: created ? [{ workspace: session.workspace, sessions: [session] }] : [] });
    if (path === "/api/sessions" && init?.method === "POST") {
      return new Promise<Response>((resolve) => { finishCreation = resolve; });
    }
    throw new Error("未配置的测试接口");
  });
  vi.stubGlobal("fetch", request);
  const onError = vi.fn();
  const onStatus = vi.fn();
  const { result } = renderHook(() => useWorkspaceSessions({ onError, onStatus }));
  await waitFor(() => expect(result.current.defaultWorkspace).toBe("/default"));
  let operation!: Promise<boolean>;
  await act(async () => { operation = result.current.selectWorkspace(); });
  expect(result.current.selectingWorkspace).toBe(true);
  expect(result.current.activeWorkspace).toBe(session.workspace);
  await act(async () => { expect(await result.current.selectWorkspace()).toBe(false); });
  created = true;
  await act(async () => { finishCreation(new Response(JSON.stringify(session))); await operation; });
  expect(result.current.selectingWorkspace).toBe(false);
  expect(result.current.activeSession?.workspace).toBe(session.workspace);
  expect(onError).toHaveBeenCalledWith("");
});
