import { apiJson } from "../../shared/api";
import type { Session, Workspace } from "../../shared/model";

export async function fetchWorkspaces(): Promise<Workspace[]> {
  return (await apiJson<{ workspaces: Workspace[] }>("/workspaces")).workspaces;
}

export async function chooseLocalWorkspace(): Promise<string | null> {
  return (await apiJson<{ workspace: string | null }>("/workspaces/select", { method: "POST" }))
    .workspace;
}

export async function createWorkspaceSession(workspace: string): Promise<Session> {
  return apiJson<Session>("/sessions", {
    method: "POST",
    body: JSON.stringify({ name: "新对话", workspace }),
  });
}

export async function removeSession(sessionId: string): Promise<void> {
  await apiJson<void>(`/sessions/${sessionId}`, { method: "DELETE" });
}

export async function saveSessionModel(sessionId: string, modelId: string): Promise<void> {
  await apiJson<Session>(`/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ model_id: modelId }),
  });
}
