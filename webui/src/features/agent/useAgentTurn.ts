import { useCallback, useEffect, useRef, useState } from "react";

import type { Activity, Approval, Message, PermissionMode } from "../../shared/model";
import { errorText } from "../../shared/presentation";
import {
  abortAgentRun,
  fetchConversation,
  fetchPendingApprovals,
  steerAgentRun,
  streamAgentRun,
  streamAgentResume,
  submitApproval,
} from "./api";
import { projectAgentEvent } from "./streamProjection";

type Options = {
  activeId: string | null;
  activeWorkspace: string | null;
  refreshWorkspaces: (
    preferredWorkspace?: string | null,
    preferredSessionId?: string | null,
  ) => Promise<void>;
  onError: (message: string) => void;
  onStatus: (message: string) => void;
};

export function useAgentTurn(options: Options) {
  const { activeId, activeWorkspace, refreshWorkspaces, onError, onStatus } = options;
  const [messages, setMessages] = useState<Message[]>([]);
  const [running, setRunning] = useState(false);
  const [streamAssistantId, setStreamAssistantId] = useState<string | null>(null);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [resolvingApprovalId, setResolvingApprovalId] = useState<string | null>(null);
  const lastSequence = useRef(0);
  const streamAssistantIdRef = useRef<string | null>(null);

  useEffect(() => {
    let current = true;
    setMessages([]);
    streamAssistantIdRef.current = null;
    setStreamAssistantId(null);
    if (activeId) {
      void fetchConversation(activeId)
        .then((loaded) => {
          if (current) setMessages(loaded);
        })
        .catch((reason: unknown) => onError(errorText(reason, "加载会话失败")));
    }
    return () => {
      current = false;
    };
  }, [activeId, onError]);

  useEffect(() => {
    const refresh = () =>
      fetchPendingApprovals()
        .then(setApprovals)
        .catch(() => undefined);
    void refresh();
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("focus", refresh);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("focus", refresh);
    };
  }, [activeId]);

  useEffect(() => {
    const pending = approvals.filter(
      (approval) => !approval.session_id || approval.session_id === activeId,
    );
    if (!activeId || !pending.length) return;
    setMessages((current) => {
      const known = new Set(
        current
          .filter((message) => message.role === "approval")
          .map((message) => message.approval?.request_id),
      );
      const additions = pending
        .filter((approval) => !known.has(approval.request_id))
        .map((approval) => ({
          id: `approval-${approval.request_id}`,
          role: "approval" as const,
          content: [],
          approval,
        }));
      return additions.length ? [...current, ...additions] : current;
    });
  }, [activeId, approvals]);

  const sendPrompt = useCallback(
    async (prompt: string, permissionMode: PermissionMode, thinking: boolean, modelId: string) => {
      const value = prompt.trim();
      if (!value || !activeId || !modelId) return false;
      onError("");
      if (running) {
        try {
          await steerAgentRun(activeId, value);
          onStatus("运行中输入已排队…");
          return true;
        } catch (reason) {
          onError(errorText(reason, "追加运行中输入失败"));
          return false;
        }
      }
      setRunning(true);
      onStatus("正在连接 Agent…");
      setActivities([]);
      lastSequence.current = 0;
      streamAssistantIdRef.current = null;
      setStreamAssistantId(null);
      const assistantIds = new Map<string, string>();
      const toolIds = new Map<string, string>();
      try {
        await streamAgentRun(activeId, { prompt: value, permissionMode, thinking, modelId }, (event) =>
          projectAgentEvent(event, assistantIds, toolIds, {
            activeId,
            lastSequence,
            streamAssistantId: streamAssistantIdRef,
            setMessages,
            setStreamAssistantId,
            setActivities,
            setApprovals,
            onError,
            onStatus,
          }),
        );
        setMessages(await fetchConversation(activeId));
        await refreshWorkspaces(activeWorkspace, activeId);
        return true;
      } catch (reason) {
        onError(errorText(reason, "发送失败"));
        onStatus("连接失败");
        return false;
      } finally {
        setRunning(false);
      }
    },
    [activeId, activeWorkspace, onError, onStatus, refreshWorkspaces, running],
  );

  const abortRun = useCallback(async () => {
    if (!activeId || !running) return;
    try {
      await abortAgentRun(activeId);
      onStatus("正在停止 Agent…");
    } catch (reason) {
      onError(errorText(reason, "停止 Agent 失败"));
    }
  }, [activeId, onError, onStatus, running]);

  const resumeRun = useCallback(
    async (permissionMode: PermissionMode, thinking: boolean, modelId: string) => {
      if (!activeId || !modelId || running) return;
      onError("");
      setRunning(true);
      onStatus("正在恢复 Agent…");
      setActivities([]);
      lastSequence.current = 0;
      streamAssistantIdRef.current = null;
      setStreamAssistantId(null);
      const assistantIds = new Map<string, string>();
      const toolIds = new Map<string, string>();
      try {
        await streamAgentResume(activeId, { permissionMode, thinking, modelId }, (event) =>
          projectAgentEvent(event, assistantIds, toolIds, {
            activeId,
            lastSequence,
            streamAssistantId: streamAssistantIdRef,
            setMessages,
            setStreamAssistantId,
            setActivities,
            setApprovals,
            onError,
            onStatus,
          }),
        );
        setMessages(await fetchConversation(activeId));
        await refreshWorkspaces(activeWorkspace, activeId);
      } catch (reason) {
        onError(errorText(reason, "恢复失败"));
        onStatus("恢复失败");
      } finally {
        setRunning(false);
      }
    },
    [activeId, activeWorkspace, onError, onStatus, refreshWorkspaces, running],
  );

  const resolveApproval = useCallback(
    async (requestId: string, approved: boolean) => {
      setResolvingApprovalId(requestId);
      try {
        await submitApproval(requestId, approved);
        setApprovals((current) =>
          current.filter((approval) => approval.request_id !== requestId),
        );
        setMessages((current) =>
          current.filter(
            (message) =>
              !(message.role === "approval" && message.approval?.request_id === requestId),
          ),
        );
      } catch (reason) {
        onError(errorText(reason, "处理审批失败"));
      } finally {
        setResolvingApprovalId(null);
      }
    },
    [onError],
  );

  const conversation = messages.filter(
    (message) => message.role === "user" || message.role === "assistant" || message.role === "toolResult",
  );
  const lastMessage = conversation[conversation.length - 1];
  const canResume =
    !running && (lastMessage?.role === "user" || lastMessage?.role === "toolResult");

  return {
    messages,
    running,
    streamAssistantId,
    activities,
    resolvingApprovalId,
    sendPrompt,
    abortRun,
    resumeRun,
    canResume,
    resolveApproval,
  };
}
