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
  const [draftModelId, setDraftModelId] = useState<string | null>(null);
  const onError = useCallback((message: string) => setError(message || null), []);
  const onStatus = useCallback((message: string) => setStatus(message), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  const workspace = useWorkspaceSessions({ onError, onStatus });
  const webSettings = useWebSettings(onError);
  const agent = useAgentTurn({
    activeId: workspace.activeId,
    activeWorkspace: workspace.activeWorkspace,
    createDefaultChatSession: workspace.createDefaultChatSession,
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

  const start = () => void workspace.createDefaultChatSession()
    .then((session) => workspace.refresh(session.workspace, session.session_id))
    .catch((reason: unknown) => onError(reason instanceof Error ? reason.message : "创建会话失败"));

  const models = webSettings.settings?.models ?? [];
  const selectedModel =
    models.find((model) => model.id === (workspace.activeSession?.model_id ?? draftModelId) && model.configured) ??
    models.find((model) => model.id === webSettings.settings?.default_model_id && model.configured) ??
    models.find((model) => model.configured);
  const chatWorkspace = workspace.activeSession?.workspace ?? workspace.defaultWorkspace;

  return (
    <div className="app-shell">
      <WorkspaceSidebar
        workspaces={workspace.workspaces}
        activeWorkspace={workspace.activeWorkspace}
        activeId={workspace.activeId}
        selectingWorkspace={workspace.selectingWorkspace}
        newChatDisabled={agent.running}
        onSelectWorkspace={() => {
          setDraftModelId(null);
          void workspace.selectWorkspace();
        }}
        onNewChat={() => {
          workspace.setActiveId(null);
          workspace.setActiveWorkspace(null);
          setDraftModelId(null);
        }}
        onActivateWorkspace={(item) => {
          setDraftModelId(null);
          workspace.activateWorkspace(item);
        }}
        onActivateSession={(workspacePath, sessionId) => {
          setDraftModelId(null);
          workspace.setActiveWorkspace(workspacePath);
          workspace.setActiveId(sessionId);
        }}
        onCreateSession={(workspacePath) => {
          setDraftModelId(null);
          void workspace.createSession(workspacePath);
        }}
        onDeleteSession={(sessionId, name) => setDeleteTarget({ sessionId, name })}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <main className="main-area">
        <header className="main-header">
          <div className="main-header-meta">
            <div className="main-header-title">
              {workspace.activeSession?.name ??
                "新对话"}
            </div>
            <div className="main-header-sub">
              <span className="online-dot" />
              {chatWorkspace ?? "正在获取默认工作区…"}
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
            activeWorkspace={chatWorkspace}
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
          active={true}
          activeWorkspace={chatWorkspace}
          running={agent.running}
          switchingModel={workspace.switchingModel}
          status={status}
          models={models}
          selectedModelId={selectedModel?.id ?? ""}
          thinking={webSettings.settings?.thinking ?? false}
          onModelChange={(modelId) => {
            if (workspace.activeSession) void workspace.changeModel(modelId);
            else setDraftModelId(modelId);
          }}
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
