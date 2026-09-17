import { useCallback, useEffect, useState } from "react";

import { Composer } from "../features/agent/Composer";
import { MessageList } from "../features/agent/MessageList";
import { TrajectoryView } from "../features/agent/TrajectoryView";
import { useAgentTurn } from "../features/agent/useAgentTurn";
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
  const onError = useCallback((message: string) => setError(message || null), []);
  const onStatus = useCallback((message: string) => setStatus(message), []);

  const workspace = useWorkspaceSessions({ onError, onStatus });
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
          status={status}
          onSend={agent.sendPrompt}
        />
      </main>

      {error && <ErrorToast message={error} onClose={() => setError(null)} />}
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
