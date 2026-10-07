import { useCallback, useEffect, useRef, useState } from "react";
import type { SetStateAction } from "react";

import type { AgentEvent, Approval, Message, PermissionMode, Session, SessionIndicator, TokenUsage } from "../../shared/model";
import { errorText } from "../../shared/presentation";
import {
  abortAgentRun, fetchConversation, fetchPendingApprovals, steerAgentRun,
  streamAgentRun, streamAgentResume, submitApproval,
} from "./api";
import { projectAgentEvent } from "./streamProjection";

type Options = {
  activeId: string | null;
  activeWorkspace: string | null;
  createDefaultChatSession: (workspace?: string) => Promise<Session>;
  activateSession: (session: Session) => void;
  refreshWorkspaces: () => Promise<void>;
};

type ConversationState = {
  messages: Message[];
  running: boolean;
  starting: boolean;
  loaded: boolean;
  streamAssistantId: string | null;
  usage: TokenUsage;
  approvals: Approval[];
  resolvingApprovalId: string | null;
  status: string;
  error: string | null;
  endStatus: "completed" | "failed" | "cancelled" | null;
  unread: boolean;
  prompt: string;
  permissionMode: PermissionMode;
};

type ConversationRuntime = {
  state: ConversationState;
  history: Promise<void> | null;
  controller: AbortController | null;
  approvalVersion: number;
};

const DRAFT = "__new_conversation__";

function emptyState(): ConversationState {
  return {
    messages: [], running: false, starting: false, loaded: false,
    streamAssistantId: null, usage: { input: null, output: null, runTotal: null },
    approvals: [], resolvingApprovalId: null, status: "准备就绪", error: null,
    endStatus: null, unread: false, prompt: "", permissionMode: "default",
  };
}

function apply<T>(value: T, action: SetStateAction<T>): T {
  return typeof action === "function" ? (action as (previous: T) => T)(value) : action;
}

export function useAgentTurn(options: Options) {
  const { activeId, activeWorkspace, createDefaultChatSession, activateSession, refreshWorkspaces } = options;
  const runtimes = useRef(new Map<string, ConversationRuntime>());
  const [views, setViews] = useState<Record<string, ConversationState>>({});
  const [creatingSession, setCreatingSession] = useState(false);
  const creating = useRef(false);
  const alive = useRef(true);
  const approvalsInitialized = useRef(false);
  const selection = useRef({ id: activeId, version: 0 });
  if (selection.current.id !== activeId) {
    selection.current = { id: activeId, version: selection.current.version + 1 };
  }

  const getRuntime = useCallback((id: string) => {
    let runtime = runtimes.current.get(id);
    if (!runtime) {
      runtime = { state: emptyState(), history: null, controller: null, approvalVersion: 0 };
      runtimes.current.set(id, runtime);
    }
    return runtime;
  }, []);

  // 引用内同步更新可阻止重复启动；React 快照只负责展示，不随选中会话迁移运行状态。
  const update = useCallback((id: string, action: (state: ConversationState) => ConversationState) => {
    const runtime = getRuntime(id);
    runtime.state = action(runtime.state);
    const next = runtime.state;
    if (alive.current) setViews((current) => ({ ...current, [id]: next }));
  }, [getRuntime]);

  const loadHistory = useCallback((id: string): Promise<void> => {
    const runtime = getRuntime(id);
    if (runtime.state.loaded) return Promise.resolve();
    if (runtime.history) return runtime.history;
    runtime.history = fetchConversation(id).then((messages) => {
      update(id, (state) => ({ ...state, loaded: true, messages: [
        ...messages, ...state.messages.filter((message) => message.role === "approval"),
      ] }));
    }).finally(() => { runtime.history = null; });
    return runtime.history;
  }, [getRuntime, update]);

  const refreshApprovals = useCallback(async () => {
    const versions = new Map([...runtimes.current].map(([id, runtime]) => [id, runtime.approvalVersion]));
    const pending = await fetchPendingApprovals();
    const ids = new Set([...runtimes.current.keys(), ...pending.map((item) => item.session_id).filter((id): id is string => !!id)]);
    for (const id of ids) {
      if (id === DRAFT) continue;
      const runtime = getRuntime(id);
      // 查询开始后的 SSE/审批操作比这份快照更新，不能被迟到的响应覆盖。
      if (runtime.approvalVersion !== (versions.get(id) ?? 0)) continue;
      const approvals = pending.filter((item) => item.session_id === id);
      update(id, (state) => {
        const requestIds = new Set(approvals.map((item) => item.request_id));
        const messages = state.messages.filter((message) => message.role !== "approval" || requestIds.has(message.approval!.request_id));
        const known = new Set(messages.filter((message) => message.role === "approval").map((message) => message.approval!.request_id));
        return { ...state, approvals, messages: [...messages, ...approvals.filter((item) => !known.has(item.request_id)).map((approval): Message => ({
          id: `approval-${approval.request_id}`, role: "approval", content: [], approval,
        }))] };
      });
    }
  }, [getRuntime, update]);

  useEffect(() => {
    alive.current = true;
    // 审批由 SSE 持续同步；初始化补快照一次，StrictMode 重放 effect 时不重复查询。
    if (!approvalsInitialized.current) {
      approvalsInitialized.current = true;
      void refreshApprovals().catch(() => undefined);
    }
    return () => {
      alive.current = false;
      for (const runtime of runtimes.current.values()) runtime.controller?.abort();
    };
  }, [refreshApprovals]);

  useEffect(() => {
    if (!activeId) return;
    update(activeId, (state) => ({ ...state, unread: false }));
    void loadHistory(activeId).catch((reason: unknown) => {
      update(activeId, (state) => ({ ...state, error: errorText(reason, "加载会话失败") }));
    });
  }, [activeId, loadHistory, update]);

  const startRun = useCallback(async (
    id: string,
    request: { prompt: string; permissionMode: PermissionMode; thinking: boolean; modelId: string },
    resume = false,
  ): Promise<boolean> => {
    const runtime = getRuntime(id);
    if (runtime.state.running) return false;
    const controller = new AbortController();
    runtime.controller = controller;
    update(id, (state) => ({ ...state, running: true, starting: true, error: null, endStatus: null,
      unread: false, status: resume ? "正在恢复 Agent…" : "正在连接 Agent…",
      streamAssistantId: null, usage: { input: null, output: null, runTotal: null },
    }));
    let accept!: (accepted: boolean) => void;
    const connected = new Promise<boolean>((resolve) => { accept = resolve; });
    const lastSequence = { current: 0 };
    const streamAssistantId = { current: null as string | null };
    const assistantIds = new Map<string, string>();
    let runId: string | null = null;
    const setField = <K extends keyof ConversationState>(key: K) => (action: SetStateAction<ConversationState[K]>) => {
      update(id, (state) => ({ ...state, [key]: apply(state[key], action) }));
    };
    const onEvent = (event: AgentEvent) => {
      if (event.session_id !== id || (runId !== null && runId !== event.run_id)) throw new Error("收到不属于当前会话运行的事件");
      if (event.sequence <= lastSequence.current) return;
      runId = event.run_id;
      if (event.type === "approval_requested" || event.type === "tool_execution_end" || event.type === "agent_end") runtime.approvalVersion++;
      projectAgentEvent(event, assistantIds, {
        activeId: id, lastSequence, streamAssistantId,
        setMessages: setField("messages"), setStreamAssistantId: setField("streamAssistantId"),
        setUsage: setField("usage"), setApprovals: setField("approvals"),
        onError: (error) => update(id, (state) => ({ ...state, error })),
        onStatus: (status) => update(id, (state) => ({ ...state, status })),
      });
      if (event.type === "approval_requested" || event.type === "agent_end") {
        update(id, (state) => ({ ...state, unread: selection.current.id !== id }));
      }
      if (event.type === "agent_end") {
        update(id, (state) => ({ ...state, endStatus: event.status, starting: true }));
      }
    };
    void (async () => {
      try {
        await loadHistory(id);
        const streamOptions = {
          signal: controller.signal,
          onConnected: () => {
            update(id, (state) => ({ ...state, starting: false }));
            accept(true);
          },
        };
        if (resume) await streamAgentResume(id, request, onEvent, streamOptions);
        else await streamAgentRun(id, request, onEvent, streamOptions);
        const messages = await fetchConversation(id);
        update(id, (state) => ({ ...state, messages, loaded: true }));
      } catch (reason) {
        if (alive.current) {
          update(id, (state) => ({ ...state, error: errorText(reason, state.endStatus ? "刷新会话消息失败" : "运行连接失败"),
            status: state.endStatus ? state.status : "运行失败",
            endStatus: state.endStatus ?? "failed", unread: selection.current.id !== id,
            streamAssistantId: null,
            messages: state.messages.filter((message) => message.id !== streamAssistantId.current),
          }));
        }
      } finally {
        accept(false);
        runtime.controller = null;
        runtime.approvalVersion++;
        update(id, (state) => ({ ...state, running: false, starting: false, approvals: [],
          messages: state.messages.filter((message) => message.role !== "approval"),
        }));
        // 后台会话完成只刷新列表，不能改写用户此刻的选择。
        if (alive.current) void refreshWorkspaces().catch(() => undefined);
      }
    })();
    return connected;
  }, [getRuntime, loadHistory, refreshWorkspaces, update]);

  const sendPrompt = useCallback(async (prompt: string, permissionMode: PermissionMode, thinking: boolean, modelId: string) => {
    const value = prompt.trim();
    if (!value || !modelId) return false;
    if (activeId) {
      const state = getRuntime(activeId).state;
      if (state.starting) return false;
      if (state.running) {
        try {
          await steerAgentRun(activeId, value);
          update(activeId, (current) => ({ ...current, status: current.endStatus ? current.status : "运行中输入已排队…", error: null }));
          return true;
        } catch (reason) {
          update(activeId, (current) => ({ ...current, error: errorText(reason, "追加运行中输入失败") }));
          return false;
        }
      }
      return startRun(activeId, { prompt: value, permissionMode, thinking, modelId });
    }
    if (creating.current) return false;
    creating.current = true;
    setCreatingSession(true);
    const selectionVersion = selection.current.version;
    try {
      const session = await createDefaultChatSession(activeWorkspace ?? undefined);
      if (!alive.current) return false;
      update(session.session_id, (state) => ({ ...state, loaded: true, permissionMode }));
      await refreshWorkspaces();
      if (!alive.current) return false;
      if (selection.current.id === null && selection.current.version === selectionVersion) activateSession(session);
      return await startRun(session.session_id, { prompt: value, permissionMode, thinking, modelId });
    } catch (reason) {
      update(DRAFT, (state) => ({ ...state, error: errorText(reason, "创建会话失败") }));
      return false;
    } finally {
      creating.current = false;
      if (alive.current) setCreatingSession(false);
    }
  }, [activeId, activeWorkspace, activateSession, createDefaultChatSession, getRuntime, refreshWorkspaces, startRun, update]);

  const abortRun = useCallback(async () => {
    if (!activeId || !getRuntime(activeId).state.running) return;
    try {
      await abortAgentRun(activeId);
      update(activeId, (state) => ({ ...state, status: state.endStatus ? state.status : "正在停止 Agent…" }));
    } catch (reason) {
      update(activeId, (state) => ({ ...state, error: errorText(reason, "停止 Agent 失败") }));
    }
  }, [activeId, getRuntime, update]);

  const resumeRun = useCallback(async (permissionMode: PermissionMode, thinking: boolean, modelId: string) => {
    if (activeId && modelId) await startRun(activeId, { prompt: "", permissionMode, thinking, modelId }, true);
  }, [activeId, startRun]);

  const resolveApproval = useCallback(async (requestId: string, approved: boolean) => {
    if (!activeId) return;
    const runtime = getRuntime(activeId);
    if (!runtime.state.approvals.some((item) => item.request_id === requestId) || runtime.state.resolvingApprovalId) return;
    runtime.approvalVersion++;
    update(activeId, (state) => ({ ...state, resolvingApprovalId: requestId }));
    try {
      await submitApproval(requestId, approved);
      update(activeId, (state) => ({ ...state,
        approvals: state.approvals.filter((item) => item.request_id !== requestId),
        messages: state.messages.filter((message) => message.approval?.request_id !== requestId),
      }));
    } catch (reason) {
      update(activeId, (state) => ({ ...state, error: errorText(reason, "处理审批失败") }));
    } finally {
      runtime.approvalVersion++;
      update(activeId, (state) => ({ ...state, resolvingApprovalId: null }));
    }
  }, [activeId, getRuntime, update]);

  const key = activeId ?? DRAFT;
  const current = views[key] ?? emptyState();
  const conversation = current.messages.filter((message) => message.role !== "approval");
  const last = conversation[conversation.length - 1];
  const indicators: Record<string, SessionIndicator> = {};
  for (const [id, state] of Object.entries(views)) {
    if (id !== DRAFT) indicators[id] = { running: state.running, approvalCount: state.approvals.length, unread: state.unread, failed: state.endStatus === "failed", cancelled: state.endStatus === "cancelled" };
  }
  return {
    ...current, creatingSession, indicators,
    canResume: !current.running && (last?.role === "user" || last?.role === "toolResult"),
    sendPrompt, abortRun, resumeRun, resolveApproval,
    setPrompt: (action: SetStateAction<string>) => update(key, (state) => ({ ...state, prompt: apply(state.prompt, action) })),
    setPermissionMode: (permissionMode: PermissionMode) => update(key, (state) => ({ ...state, permissionMode })),
    clearError: () => update(key, (state) => ({ ...state, error: null })),
  };
}
