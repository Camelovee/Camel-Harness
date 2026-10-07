import { useCallback, useEffect, useMemo, useState } from "react";

import type { Session, Workspace } from "../../shared/model";
import { createWorkspaceSession, chooseLocalWorkspace, fetchDefaultWorkspace, fetchWorkspaces, removeSession, renameWorkspaceSession, saveSessionModel } from "./api";

type Options = {
  onError: (message: string) => void;
  onStatus: (message: string) => void;
};

export function useWorkspaceSessions({ onError, onStatus }: Options) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [defaultWorkspace, setDefaultWorkspace] = useState<string | null>(null);
  const [selectingWorkspace, setSelectingWorkspace] = useState(false);
  const [switchingModel, setSwitchingModel] = useState(false);

  const activeSession = useMemo(
    () =>
      workspaces
        .flatMap((workspace) => workspace.sessions)
        .find((session) => session.session_id === activeId),
    [activeId, workspaces],
  );

  const refresh = useCallback(
    async (preferredWorkspace?: string | null, preferredSessionId?: string | null) => {
      const loaded = await fetchWorkspaces();
      const allSessions = loaded.flatMap((workspace) => workspace.sessions);
      setWorkspaces(loaded);
      setActiveWorkspace((current) => preferredWorkspace ?? current);
      setActiveId((current) => {
        if (
          preferredSessionId &&
          allSessions.some((session) => session.session_id === preferredSessionId)
        ) {
          return preferredSessionId;
        }
        if (current && allSessions.some((session) => session.session_id === current)) return current;
        return null;
      });
    },
    [],
  );

  useEffect(() => {
    void refresh().catch((reason: unknown) =>
      onError(reason instanceof Error ? reason.message : "加载工作区失败"),
    );
    void fetchDefaultWorkspace()
      .then(setDefaultWorkspace)
      .catch((reason: unknown) => onError(reason instanceof Error ? reason.message : "加载默认工作区失败"));
  }, [onError, refresh]);

  const createForWorkspace = useCallback(
    async (workspace?: string) => {
      const session = await createWorkspaceSession(workspace);
      await refresh(session.workspace, session.session_id);
      return session;
    },
    [refresh],
  );

  const pickWorkspace = useCallback(async (): Promise<string | null> => {
    if (selectingWorkspace) return null;
    onError("");
    setSelectingWorkspace(true);
    onStatus("正在打开工作区选择器…");
    try {
      const selected = await chooseLocalWorkspace();
      if (!selected) {
        onStatus("已取消选择");
        return null;
      }
      return selected;
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "选择工作区失败");
      onStatus("工作区选择失败");
      return null;
    } finally {
      setSelectingWorkspace(false);
    }
  }, [onError, onStatus, selectingWorkspace]);

  const selectWorkspace = useCallback(async () => {
    const selected = await pickWorkspace();
    if (!selected) return false;
    const existing = workspaces.find((workspace) => workspace.workspace === selected);
    setActiveWorkspace(selected);
    setActiveId(existing?.sessions[0]?.session_id ?? null);
    if (!existing) {
      try {
        await createForWorkspace(selected);
      } catch (reason) {
        onError(reason instanceof Error ? reason.message : "创建工作区会话失败");
        return false;
      }
    }
    return true;
  }, [createForWorkspace, onError, pickWorkspace, workspaces]);

  const createSession = useCallback(
    async (workspace = activeWorkspace ?? defaultWorkspace ?? undefined) => {
      onError("");
      try {
        return await createForWorkspace(workspace);
      } catch (reason) {
        onError(reason instanceof Error ? reason.message : "创建会话失败");
        return null;
      }
    },
    [activeWorkspace, createForWorkspace, defaultWorkspace, onError],
  );

  const createDefaultChatSession = useCallback(
    (workspace?: string) => createWorkspaceSession(workspace),
    [],
  );

  const deleteSession = useCallback(
    async (session: Pick<Session, "session_id">) => {
      try {
        await removeSession(session.session_id);
        await refresh();
      } catch (reason) {
        onError(reason instanceof Error ? reason.message : "删除会话失败");
      }
    },
    [onError, refresh],
  );

  const renameSession = useCallback(async (sessionId: string, name: string) => {
    try {
      await renameWorkspaceSession(sessionId, name.trim());
      await refresh();
      return true;
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "重命名会话失败");
      return false;
    }
  }, [onError, refresh]);

  const activateWorkspace = useCallback((workspace: Workspace) => {
    setActiveWorkspace(workspace.workspace);
    setActiveId(workspace.sessions[0]?.session_id ?? null);
  }, []);

  const changeModel = useCallback(async (modelId: string) => {
    if (!activeId) return;
    const sessionId = activeId;
    const previousModelId = activeSession?.model_id ?? null;
    setSwitchingModel(true);
    setWorkspaces((current) => current.map((item) => ({
      ...item,
      sessions: item.sessions.map((session) => session.session_id === sessionId
        ? { ...session, model_id: modelId }
        : session),
    })));
    try {
      await saveSessionModel(sessionId, modelId);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "切换模型失败");
      setWorkspaces((current) => current.map((item) => ({
        ...item,
        sessions: item.sessions.map((session) => session.session_id === sessionId
          ? { ...session, model_id: previousModelId }
          : session),
      })));
    } finally {
      setSwitchingModel(false);
    }
  }, [activeId, activeSession?.model_id, onError]);

  return {
    workspaces,
    activeWorkspace,
    defaultWorkspace,
    activeId,
    activeSession,
    selectingWorkspace,
    switchingModel,
    setActiveWorkspace,
    setActiveId,
    refresh,
    selectWorkspace,
    pickWorkspace,
    createSession,
    createDefaultChatSession,
    deleteSession,
    renameSession,
    activateWorkspace,
    changeModel,
  };
}
