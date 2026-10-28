import { useCallback, useEffect, useRef, useState } from "react";

import { SidebarSimple, PencilSimple, SlidersHorizontal } from "@phosphor-icons/react";

import { ExecutionPanel } from "../features/agent/ExecutionPanel";
import { RunStatus, type RunState } from "../features/agent/RunStatus";
import { CamelMark } from "../shared/CamelMark";
import { ModalDialog } from "../shared/ModalDialog";
import { workspaceName } from "../shared/presentation";
import { usePanelMotion } from "./usePanelMotion";
import { Composer } from "../features/agent/Composer";
import { SubagentPanel } from "../features/agent/SubagentPanel";
import { MessageList } from "../features/agent/MessageList";
import { useAgentTurn } from "../features/agent/useAgentTurn";
import { ToolDetailsPanel } from "../features/agent/ToolDetailsPanel";
import { SettingsDialog } from "../features/settings/SettingsDialog";
import { useSettings } from "../features/settings/useSettings";
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
  const [narrowWindow, setNarrowWindow] = useState(() => window.matchMedia("(max-width: 1179px)").matches);
  const [inspectorOpen, setInspectorOpen] = useState(!narrowWindow);
  const inspectorToggleRef = useRef<HTMLButtonElement>(null);
  const { panelRef, transition } = usePanelMotion();
  const [selectedTool, setSelectedTool] = useState<Message | null>(null);
  const firstSettingsLoaded = useRef(false);
  const [draftModelId, setDraftModelId] = useState<string | null>(null);
  const [draftWorkspace, setDraftWorkspace] = useState<string | null>(null);
  const onError = useCallback((message: string) => setError(message || null), []);
  const onStatus = useCallback((message: string) => setStatus(message), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  const workspace = useWorkspaceSessions({ onError, onStatus });
  const settingsState = useSettings(onError);
  useAppearance(settingsState.settings?.theme);
  useEffect(() => {
    if (!settingsState.settings || firstSettingsLoaded.current) return;
    firstSettingsLoaded.current = true;
    if (!settingsState.settings.models.some((model) => model.configured)) setSettingsOpen(true);
  }, [settingsState.settings]);
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
  const closeTool = useCallback(() => {
    transition(() => setSelectedTool(null));
    inspectorToggleRef.current?.focus();
  }, [transition]);
  useEffect(() => { setSelectedTool(null); }, [workspace.activeId]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1179px)");
    const resize = () => {
      setNarrowWindow(media.matches);
      if (media.matches) { setInspectorOpen(false); setSelectedTool(null); agent.closeSubagent(); }
    };
    media.addEventListener("change", resize);
    return () => media.removeEventListener("change", resize);
  }, [agent.closeSubagent]);
  useEffect(() => {
    if (narrowWindow && inspectorOpen) document.getElementById("execution-inspector")?.querySelector("button")?.focus();
  }, [narrowWindow, inspectorOpen, selectedTool, agent.selectedSubagentId]);

  function closeInspector() {
    transition(() => { setInspectorOpen(false); setSelectedTool(null); agent.closeSubagent(); });
    inspectorToggleRef.current?.focus();
  }
  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape" && inspectorOpen && !settingsOpen) closeInspector();
    };
    window.addEventListener("keydown", dismiss);
    return () => window.removeEventListener("keydown", dismiss);
  }, [inspectorOpen, settingsOpen, agent.closeSubagent]);

  function openTool(message: Message) {
    transition(() => { agent.closeSubagent(); setSelectedTool(message); setInspectorOpen(true); });
  }
  const closeSubagentPanel = useCallback(() => {
    transition(agent.closeSubagent);
    inspectorToggleRef.current?.focus();
  }, [transition, agent.closeSubagent]);
  function openSubagent(id: string) {
    transition(() => { setSelectedTool(null); setInspectorOpen(true); void agent.openSubagent(id); });
  }

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
    "toggle-sidebar": () => transition(() => setSidebarCollapsed((collapsed) => !collapsed)),
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

  const models = settingsState.settings?.models ?? [];
  const selectedModel =
    models.find((model) => model.id === (workspace.activeSession?.model_id ?? draftModelId) && model.configured) ??
    models.find((model) => model.id === settingsState.settings?.default_model_id && model.configured) ??
    models.find((model) => model.configured);
  const runState: RunState = agent.approvals.length ? "waiting" : agent.running ? "running" : agent.endStatus ?? "idle";
  const modalInspector = narrowWindow && inspectorOpen;
  const inspectorContent = (selectedTool ? <ToolDetailsPanel message={selectedTool} onClose={closeTool} />
            : agent.selectedSubagentId && agent.subagents[agent.selectedSubagentId] ?
              <SubagentPanel child={agent.subagents[agent.selectedSubagentId]} workspace={chatWorkspace} resolvingApprovalId={agent.resolvingApprovalId}
                onClose={closeSubagentPanel} onResolveApproval={(requestId, approved) => void agent.resolveApproval(requestId, approved)} />
            : <ExecutionPanel messages={agent.messages} approvals={agent.approvals} subagents={Object.values(agent.subagents)}
                running={agent.running} state={runState} phase={agent.status} usage={agent.usage} model={selectedModel}
                permissionMode={agent.permissionMode} effort={agent.effort} workspace={chatWorkspace}
                onOpenTool={openTool} onOpenSubagent={openSubagent} onClose={closeInspector} />);
  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""} ${inspectorOpen ? "inspector-open" : ""}`}>
      <a className="skip-link" href="#conversation">跳到对话</a>
      <header className="app-topbar" inert={modalInspector}>
        <div className="app-brand"><span className="app-brand-mark"><CamelMark /></span><strong>Camel <span>Harness</span></strong></div>
        <div className="app-breadcrumb" title={chatWorkspace ?? ""}><span>/</span>{chatWorkspace ? workspaceName(chatWorkspace) : "你的工作空间"}</div>
        <div className="topbar-actions">
          <span className="local-indicator"><span />本机工作区</span>
          <button className="icon-button" aria-controls="workspace-navigation" aria-expanded={!sidebarCollapsed}
            aria-label={sidebarCollapsed ? "展开侧栏" : "收起侧栏"} title="切换侧栏（⌘ / Ctrl + B）"
            onClick={() => transition(() => setSidebarCollapsed((collapsed) => !collapsed))}><SidebarSimple size={20} aria-hidden="true" /></button>
          <button ref={inspectorToggleRef} className={`icon-button ${inspectorOpen ? "is-active" : ""}`} aria-controls={inspectorOpen ? "execution-inspector" : undefined} aria-expanded={inspectorOpen}
            aria-label={inspectorOpen ? "收起执行详情" : "展开执行详情"} title="切换执行详情"
            onClick={() => {
              if (inspectorOpen) closeInspector();
              else transition(() => setInspectorOpen(true));
            }}><SidebarSimple mirrored size={20} aria-hidden="true" /></button>
          <span className="toolbar-divider" />
          <button className="icon-button" aria-label="打开设置" title="设置（⌘ / Ctrl + ,）" onClick={() => setSettingsOpen(true)}><SlidersHorizontal size={20} aria-hidden="true" /></button>
        </div>
      </header>

      <div className="workspace-layout">
        <div id="workspace-navigation" className="sidebar-container" hidden={sidebarCollapsed} inert={modalInspector}>
          <WorkspaceSidebar workspaces={workspace.workspaces} activeWorkspace={workspace.activeWorkspace} activeId={workspace.activeId}
            selectingWorkspace={workspace.selectingWorkspace} newChatDisabled={agent.creatingSession} indicators={agent.indicators}
            onSelectWorkspace={openProject} onNewChat={newChat}
            onActivateWorkspace={(item) => { setDraftModelId(null); setDraftWorkspace(null); workspace.activateWorkspace(item); }}
            onActivateSession={(workspacePath, sessionId) => {
              setDraftModelId(null); setDraftWorkspace(null); workspace.setActiveWorkspace(workspacePath); workspace.setActiveId(sessionId);
            }}
            onCreateSession={(workspacePath) => { setDraftModelId(null); setDraftWorkspace(null); void workspace.createSession(workspacePath); }}
            onDeleteSession={(sessionId, name) => setDeleteTarget({ sessionId, name })}
            onRenameSession={(sessionId, name) => setRenameTarget({ sessionId, name })} onOpenSettings={() => setSettingsOpen(true)} />
        </div>

        <main className="main-area" id="conversation" tabIndex={-1} ref={panelRef} inert={modalInspector}>
          <header className="main-header">
            <div className="main-header-meta">
              <span className="eyebrow">CONVERSATION</span>
              <div className="main-header-title-row"><h1 className="main-header-title">{workspace.activeSession?.name ?? "新对话"}</h1>
                {workspace.activeSession && <button className="icon-button header-rename" aria-label="重命名会话" title="重命名会话"
                  onClick={() => setRenameTarget({ sessionId: workspace.activeSession!.session_id, name: workspace.activeSession!.name })}><PencilSimple size={15} aria-hidden="true" /></button>}
              </div>
              <div className="main-header-sub" title={chatWorkspace ?? ""}>{chatWorkspace ?? "正在获取默认工作区…"}</div>
            </div>
            <RunStatus state={runState} announce />
          </header>

          {settingsState.settings && !selectedModel && <div className="model-onboarding" role="region" aria-label="开始使用">
            <div><strong>先连接你的模型</strong><p>配置完成后，选择项目即可开始。</p></div>
            <button className="primary-button" onClick={() => setSettingsOpen(true)}>配置模型</button>
          </div>}

          <MessageList key={`messages-${workspace.activeId ?? "new-chat"}`} activeSession={workspace.activeSession} activeWorkspace={chatWorkspace}
            messages={agent.messages} running={agent.running} streamAssistantId={agent.streamAssistantId}
            resolvingApprovalId={agent.resolvingApprovalId} onResolveApproval={(requestId, approved) => void agent.resolveApproval(requestId, approved)}
            onStart={start} subagents={Object.values(agent.subagents)} onOpenSubagent={openSubagent} onOpenTool={openTool}
            onSuggest={(prompt) => { agent.setPrompt(prompt); document.getElementById("message-input")?.focus(); }} />
          <Composer key={`composer-${workspace.activeId ?? "new-chat"}`} active={!workspace.selectingWorkspace && settingsState.settings !== null}
            activeWorkspace={chatWorkspace} running={agent.running} switchingModel={workspace.switchingModel}
            status={workspace.selectingWorkspace ? status : agent.status} prompt={agent.prompt} onPromptChange={agent.setPrompt}
            permissionMode={agent.permissionMode} onPermissionModeChange={agent.setPermissionMode}
            starting={agent.starting || (workspace.activeId === null && agent.creatingSession)} models={models} selectedModelId={selectedModel?.id ?? ""}
            effort={agent.effort} onEffortChange={agent.setEffort} choosingWorkspace={workspace.selectingWorkspace}
            onChooseWorkspace={() => {
              void workspace.pickWorkspace().then((selected) => {
                if (!selected) return;
                workspace.setActiveId(null); workspace.setActiveWorkspace(null); setDraftModelId(null); setDraftWorkspace(selected);
                onStatus(`新对话将在 ${selected} 中工作`);
              });
            }}
            onModelChange={(modelId) => { if (workspace.activeSession) void workspace.changeModel(modelId); else setDraftModelId(modelId); }}
            onSend={agent.sendPrompt} canResume={agent.canResume} onAbort={agent.abortRun} onResume={agent.resumeRun} />
        </main>

        {inspectorOpen && (modalInspector ?
          <ModalDialog id="execution-inspector" className="inspector-host inspector-drawer" labelledBy="inspector-dialog-title" onClose={closeInspector}>
            <h2 id="inspector-dialog-title" className="sr-only">执行详情面板</h2>{inspectorContent}
          </ModalDialog> : <div className="inspector-host" id="execution-inspector">{inspectorContent}</div>)}

      </div>

      {(error || agent.error) && <ErrorToast message={error || agent.error!} onClose={() => { setError(null); agent.clearError(); }} />}
      {settingsOpen && (
        <SettingsDialog
          settings={settingsState.settings}
          saving={settingsState.saving}
          onClose={closeSettings}
          onSavePreferences={settingsState.savePreferences}
          onSaveTheme={settingsState.saveTheme}
          onSaveModel={settingsState.saveModel}
          onDeleteModel={async (modelId) => {
            const saved = await settingsState.deleteModel(modelId);
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
