import { useCallback, useEffect, useRef, useState } from "react";

import { Composer } from "../features/agent/Composer";
import { SubagentPanel } from "../features/agent/SubagentPanel";
import { MessageList } from "../features/agent/MessageList";
import { useAgentTurn } from "../features/agent/useAgentTurn";
import { ToolDetailsPanel } from "../features/agent/ToolDetailsPanel";
import { SettingsDialog } from "../features/settings/SettingsDialog";
import { useWebSettings } from "../features/settings/useWebSettings";
import { useAppearance } from "../features/settings/useAppearance";
import { WorkspaceSidebar } from "../features/workspaces/WorkspaceSidebar";
import { useWorkspaceSessions } from "../features/workspaces/useWorkspaceSessions";
import { DeleteSessionDialog, ErrorToast, RenameSessionDialog } from "../shared/Feedback";
import type { Message } from "../shared/model";
import { useAppShortcuts } from "./useAppShortcuts";

type DeleteTarget = { sessionId: string; name: string } | null;
type RenameTarget = { sessionId: string; name: string } | null;

export default function DesktopApp() {
  const [status, setStatus] = useState("准备就绪");
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget>(null);
  const [renameTarget, setRenameTarget] = useState<RenameTarget>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [selectedTool, setSelectedTool] = useState<Message | null>(null);
  const firstSettingsLoaded = useRef(false);
  const [draftModelId, setDraftModelId] = useState<string | null>(null);
  const [draftWorkspace, setDraftWorkspace] = useState<string | null>(null);
  const onError = useCallback((message: string) => setError(message || null), []);
  const onStatus = useCallback((message: string) => setStatus(message), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  const workspace = useWorkspaceSessions({ onError, onStatus });
  const webSettings = useWebSettings(onError);
  useAppearance(webSettings.settings?.theme);
  useEffect(() => {
    if (!webSettings.settings || firstSettingsLoaded.current) return;
    firstSettingsLoaded.current = true;
    if (!webSettings.settings.models.some((model) => model.configured)) setSettingsOpen(true);
  }, [webSettings.settings]);
  const chatWorkspace = workspace.activeSession?.workspace ?? draftWorkspace ?? workspace.activeWorkspace ?? workspace.defaultWorkspace;
  const agent = useAgentTurn({
    activeId: workspace.activeId,
    activeWorkspace: chatWorkspace,
    createDefaultChatSession: workspace.createDefaultChatSession,
    activateSession: (session) => {
      workspace.setActiveWorkspace(session.workspace);
      workspace.setActiveId(session.session_id);
    },
    refreshWorkspaces: workspace.refresh,
  });
  const closeTool = useCallback(() => setSelectedTool(null), []);
  useEffect(() => { setSelectedTool(null); }, [workspace.activeId]);
  useEffect(() => {
    const narrowWindow = window.matchMedia?.("(max-width: 1179px)");
    const closeDetails = () => {
      if (narrowWindow?.matches) { setSelectedTool(null); agent.closeSubagent(); }
    };
    narrowWindow?.addEventListener("change", closeDetails);
    return () => narrowWindow?.removeEventListener("change", closeDetails);
  }, [agent.closeSubagent]);

  const newChat = () => {
    workspace.setActiveId(null);
    workspace.setActiveWorkspace(null);
    setDraftModelId(null);
    setDraftWorkspace(null);
    setSelectedTool(null);
    agent.closeSubagent();
  };
  const openProject = () => {
    void workspace.selectWorkspace().then((selected) => {
      if (selected) { setDraftModelId(null); setDraftWorkspace(null); }
    });
  };
  useAppShortcuts({
    "new-chat": () => { setSettingsOpen(false); newChat(); },
    "open-project": openProject,
    settings: () => setSettingsOpen(true),
    "toggle-sidebar": () => setSidebarCollapsed((collapsed) => !collapsed),
  });

  async function confirmDeleteSession() {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setDeleteTarget(null);
    await workspace.deleteSession({ session_id: target.sessionId });
  }

  const start = () => void workspace.createDefaultChatSession(chatWorkspace ?? undefined)
    .then((session) => workspace.refresh(session.workspace, session.session_id))
    .catch((reason: unknown) => onError(reason instanceof Error ? reason.message : "创建会话失败"));

  const models = webSettings.settings?.models ?? [];
  const selectedModel =
    models.find((model) => model.id === (workspace.activeSession?.model_id ?? draftModelId) && model.configured) ??
    models.find((model) => model.id === webSettings.settings?.default_model_id && model.configured) ??
    models.find((model) => model.configured);
  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <div id="workspace-navigation" className="sidebar-container" hidden={sidebarCollapsed}>
      <WorkspaceSidebar
        workspaces={workspace.workspaces}
        activeWorkspace={workspace.activeWorkspace}
        activeId={workspace.activeId}
        selectingWorkspace={workspace.selectingWorkspace}
        newChatDisabled={agent.creatingSession}
        indicators={agent.indicators}
        onSelectWorkspace={openProject}
        onNewChat={newChat}
        onActivateWorkspace={(item) => {
          setDraftModelId(null);
          setDraftWorkspace(null);
          workspace.activateWorkspace(item);
        }}
        onActivateSession={(workspacePath, sessionId) => {
          setDraftModelId(null);
          setDraftWorkspace(null);
          workspace.setActiveWorkspace(workspacePath);
          workspace.setActiveId(sessionId);
        }}
        onCreateSession={(workspacePath) => {
          setDraftModelId(null);
          setDraftWorkspace(null);
          void workspace.createSession(workspacePath);
        }}
        onDeleteSession={(sessionId, name) => setDeleteTarget({ sessionId, name })}
        onRenameSession={(sessionId, name) => setRenameTarget({ sessionId, name })}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      </div>

      <main className="main-area">
        <header className="main-header">
          <button className="sidebar-toggle" type="button" aria-controls="workspace-navigation"
            aria-expanded={!sidebarCollapsed} aria-label={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}
            title="切换侧栏（⌘ / Ctrl + B）" onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}>☰</button>
          <div className="main-header-meta">
            <div className="main-header-title-row">
              <div className="main-header-title">{workspace.activeSession?.name ?? "新对话"}</div>
              {workspace.activeSession && (
                <button type="button" className="header-rename" onClick={() => setRenameTarget({ sessionId: workspace.activeSession!.session_id, name: workspace.activeSession!.name })} title="重命名会话" aria-label="重命名会话">✎</button>
              )}
            </div>
            <div className="main-header-sub">
              <span className="online-dot" />
              {chatWorkspace ?? "正在获取默认工作区…"}
            </div>
          </div>
          <details className="usage-disclosure"><summary>用量</summary>
          <div className="usage-summary" aria-label="Token 用量">
            <span title="最近一次模型请求的输入 Token，包含发给模型的上下文；不是模型上下文窗口占比">上下文输入 <strong>{agent.usage.input?.toLocaleString() ?? "—"}</strong></span>
            <span title="最近一次模型响应的输出 Token">最近输出 <strong>{agent.usage.output?.toLocaleString() ?? "—"}</strong></span>
            <span title="本次 Agent Run 的总 Token（含压缩调用），不是会话历史累计">本轮总量 <strong>{agent.usage.runTotal?.toLocaleString() ?? "—"}</strong></span>
          </div>
          </details>
        </header>

        {webSettings.settings && !selectedModel && <div className="model-onboarding" role="region" aria-label="开始使用">
          <div><strong>连接模型，开始协作</strong><p>配置你的模型服务，再选择一个本地项目。</p></div>
          <button className="primary-button" onClick={() => setSettingsOpen(true)}>配置模型</button>
        </div>}

        <MessageList
            key={`messages-${workspace.activeId ?? "new-chat"}`}
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
            subagents={Object.values(agent.subagents)}
            onOpenSubagent={(id) => { setSelectedTool(null); void agent.openSubagent(id); }}
            onOpenTool={(message) => { agent.closeSubagent(); setSelectedTool(message); }}
        />
        <Composer
          key={`composer-${workspace.activeId ?? "new-chat"}`}
          active={!workspace.selectingWorkspace && webSettings.settings !== null}
          activeWorkspace={chatWorkspace}
          running={agent.running}
          switchingModel={workspace.switchingModel}
          status={workspace.selectingWorkspace ? status : agent.status}
          prompt={agent.prompt}
          onPromptChange={agent.setPrompt}
          permissionMode={agent.permissionMode}
          onPermissionModeChange={agent.setPermissionMode}
          starting={agent.starting || (workspace.activeId === null && agent.creatingSession)}
          models={models}
          selectedModelId={selectedModel?.id ?? ""}
          effort={agent.effort}
          onEffortChange={agent.setEffort}
          choosingWorkspace={workspace.selectingWorkspace}
          onChooseWorkspace={() => {
            void workspace.pickWorkspace().then((selected) => {
              if (!selected) return;
              workspace.setActiveId(null);
              workspace.setActiveWorkspace(null);
              setDraftModelId(null);
              setDraftWorkspace(selected);
              onStatus(`新对话将在 ${selected} 中工作`);
            });
          }}
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

      {selectedTool && <ToolDetailsPanel message={selectedTool} onClose={closeTool} />}

      {agent.selectedSubagentId && agent.subagents[agent.selectedSubagentId] && (
        <SubagentPanel child={agent.subagents[agent.selectedSubagentId]} workspace={chatWorkspace}
          resolvingApprovalId={agent.resolvingApprovalId} onClose={agent.closeSubagent}
          onResolveApproval={(requestId, approved) => { void agent.resolveApproval(requestId, approved); }} />
      )}

      {(error || agent.error) && <ErrorToast message={error || agent.error!} onClose={() => { setError(null); agent.clearError(); }} />}
      {settingsOpen && (
        <SettingsDialog
          settings={webSettings.settings}
          saving={webSettings.saving}
          onClose={closeSettings}
          onSavePreferences={webSettings.savePreferences}
          onSaveTheme={webSettings.saveTheme}
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
      {renameTarget && (
        <RenameSessionDialog
          name={renameTarget.name}
          onCancel={() => setRenameTarget(null)}
          onConfirm={(name) => workspace.renameSession(renameTarget.sessionId, name)}
        />
      )}
    </div>
  );
}
