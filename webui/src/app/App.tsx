import { useCallback, useEffect, useState } from "react";

import { Composer } from "../features/agent/Composer";
import { MessageList } from "../features/agent/MessageList";
import { TrajectoryView } from "../features/agent/TrajectoryView";
import { useAgentTurn } from "../features/agent/useAgentTurn";
import { SettingsDialog } from "../features/settings/SettingsDialog";
import { useWebSettings } from "../features/settings/useWebSettings";
import { WorkspaceSidebar } from "../features/workspaces/WorkspaceSidebar";
import { useWorkspaceSessions } from "../features/workspaces/useWorkspaceSessions";
import type { WorkbenchTab } from "../shared/model";
import { workspaceName } from "../shared/presentation";
import { DeleteSessionDialog, ErrorToast } from "../shared/Feedback";

type DeleteTarget = { sessionId: string; name: string } | null;

export default function App() {
  const [status, setStatus] = useState("准备就绪");
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<WorkbenchTab>("chat");
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const onError = useCallback((message: string) => setError(message || null), []);
  const onStatus = useCallback((message: string) => setStatus(message), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  const workspace = useWorkspaceSessions({ onError, onStatus });
  const webSettings = useWebSettings(onError);
  const agent = useAgentTurn({
    activeId: workspace.activeId,
    activeWorkspace: workspace.activeWorkspace,
    refreshWorkspaces: workspace.refresh,
    onError,
    onStatus,
  });

  useEffect(() => {
    setActiveTab("chat");
  }, [workspace.activeId]);

  async function confirmDeleteSession() {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setDeleteTarget(null);
    await workspace.deleteSession({ session_id: target.sessionId });
  }

  const start = () =>
    void (workspace.activeWorkspace
      ? workspace.createSession()
      : workspace.selectWorkspace());

  const models = webSettings.settings?.models ?? [];
  const selectedModel =
    models.find((model) => model.id === workspace.activeSession?.model_id && model.configured) ??
    models.find((model) => model.id === webSettings.settings?.default_model_id && model.configured) ??
    models.find((model) => model.configured);

  return (
    <div className="app-shell">
      <WorkspaceSidebar
        workspaces={workspace.workspaces}
        activeWorkspace={workspace.activeWorkspace}
        activeId={workspace.activeId}
        selectingWorkspace={workspace.selectingWorkspace}
        onSelectWorkspace={() => void workspace.selectWorkspace()}
        onActivateWorkspace={workspace.activateWorkspace}
        onActivateSession={(workspacePath, sessionId) => {
          workspace.setActiveWorkspace(workspacePath);
          workspace.setActiveId(sessionId);
        }}
        onCreateSession={(workspacePath) => void workspace.createSession(workspacePath)}
        onDeleteSession={(sessionId, name) => setDeleteTarget({ sessionId, name })}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <main className="main-area">
        <header className="main-header">
          <div className="main-header-meta">
            <div className="main-header-title">
              {workspace.activeSession?.name ??
                (workspace.activeWorkspace
                  ? workspaceName(workspace.activeWorkspace)
                  : "Camel Harness")}
            </div>
            <div className="main-header-sub">
              <span className="online-dot" />
              {workspace.activeWorkspace ?? "请选择本地工作区"}
            </div>
          </div>
          <nav className="main-header-tabs">
            <button
              className={activeTab === "chat" ? "active" : ""}
              onClick={() => setActiveTab("chat")}
            >
              对话
            </button>
            <button
              className={activeTab === "trajectory" ? "active" : ""}
              onClick={() => setActiveTab("trajectory")}
            >
              轨迹
            </button>
          </nav>
        </header>

        {activeTab === "trajectory" ? (
          <TrajectoryView activities={agent.activities} />
        ) : (
          <MessageList
            activeSession={workspace.activeSession}
            activeWorkspace={workspace.activeWorkspace}
            messages={agent.messages}
            running={agent.running}
            streamAssistantId={agent.streamAssistantId}
            resolvingApprovalId={agent.resolvingApprovalId}
            onResolveApproval={(requestId, approved) =>
              void agent.resolveApproval(requestId, approved)
            }
            onStart={start}
          />
        )}
        <Composer
          active={Boolean(workspace.activeSession)}
          activeWorkspace={workspace.activeWorkspace}
          running={agent.running}
          switchingModel={workspace.switchingModel}
          status={status}
          models={models}
          selectedModelId={selectedModel?.id ?? ""}
          thinking={webSettings.settings?.thinking ?? false}
          onModelChange={(modelId) => void workspace.changeModel(modelId)}
          onSend={agent.sendPrompt}
          canResume={agent.canResume}
          onAbort={agent.abortRun}
          onResume={agent.resumeRun}
        />
      </main>

      {error && <ErrorToast message={error} onClose={() => setError(null)} />}
      {settingsOpen && (
        <SettingsDialog
          settings={webSettings.settings}
          saving={webSettings.saving}
          onClose={closeSettings}
          onSavePreferences={webSettings.savePreferences}
          onSaveModel={webSettings.saveModel}
          onDeleteModel={async (modelId) => {
            const saved = await webSettings.deleteModel(modelId);
            if (saved) {
              try {
                await workspace.refresh();
              } catch {
                onError("模型已删除，但会话列表刷新失败，请刷新页面");
              }
            }
            return saved;
          }}
        />
      )}
      {deleteTarget && (
        <DeleteSessionDialog
          name={deleteTarget.name}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => void confirmDeleteSession()}
        />
      )}
    </div>
  );
}
