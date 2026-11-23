import { CaretRight, ChatCircle, Folder, FolderOpen, GearSix, PencilSimple, Plus, Trash, SpinnerGap } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import type { SessionIndicator, Workspace } from "../../shared/model";
import { workspaceName } from "../../shared/presentation";

type Props = {
  workspaces: Workspace[];
  activeWorkspace: string | null;
  activeId: string | null;
  selectingWorkspace: boolean;
  newChatDisabled: boolean;
  indicators: Record<string, SessionIndicator>;
  onSelectWorkspace: () => void;
  onNewChat: () => void;
  onActivateWorkspace: (workspace: Workspace) => void;
  onActivateSession: (workspace: string, sessionId: string) => void;
  onCreateSession: (workspace: string) => void;
  onDeleteSession: (sessionId: string, name: string) => void;
  onRenameSession: (sessionId: string, name: string) => void;
  onOpenSettings: () => void;
};

export function WorkspaceSidebar({ workspaces, activeWorkspace, activeId, selectingWorkspace, newChatDisabled,
  indicators, onSelectWorkspace, onNewChat, onActivateWorkspace, onActivateSession, onCreateSession, onDeleteSession,
  onRenameSession, onOpenSettings }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  // 折叠只影响导航；新选择或新建会话时重新展示对应分组。
  useEffect(() => setCollapsed(false), [activeWorkspace, activeId]);

  return <aside className="sidebar" aria-label="工作区与会话">
    <button aria-label="新对话" className="new-chat" onClick={onNewChat} disabled={newChatDisabled}><Plus size={18} aria-hidden="true" />新对话<span className="shortcut-key" aria-hidden="true">{navigator.userAgent.includes("Mac") ? "⌘N" : "Ctrl N"}</span></button>
    <button className="new-workspace" disabled={selectingWorkspace} onClick={onSelectWorkspace}>
      {selectingWorkspace ? <SpinnerGap className="spin" size={18} aria-hidden="true" /> : <FolderOpen size={18} aria-hidden="true" />}
      {selectingWorkspace ? "正在选择…" : "选择本地工作区"}
    </button>
    <h2 className="workspace-heading">工作区<span className="sidebar-section-rule" /></h2>
    <nav className="workspace-list" aria-label="工作区导航">
      {workspaces.map((workspace) => {
        const active = workspace.workspace === activeWorkspace;
        const expanded = active && !collapsed;
        const workspaceIndicators = workspace.sessions.map((session) => indicators[session.session_id]);
        const workspaceState = workspaceIndicators.some((item) => item?.approvalCount) ? "待审批"
          : workspaceIndicators.some((item) => item?.running) ? "运行中" : null;
        return <div className={`workspace-group ${active ? "active" : ""}`} key={workspace.workspace}>
        <div className="workspace-row">
          <button className="workspace-select" aria-label={`打开工作区 ${workspaceName(workspace.workspace)}`} aria-expanded={expanded} onClick={() => {
            if (active) setCollapsed((current) => !current);
            else onActivateWorkspace(workspace);
          }} title={workspace.workspace}>
            <CaretRight className="workspace-chevron" size={12} aria-hidden="true" />
            <span className="workspace-folder">{expanded ? <FolderOpen size={19} aria-hidden="true" /> : <Folder size={19} aria-hidden="true" />}</span>
            <span className="workspace-copy"><strong>{workspaceName(workspace.workspace)}</strong>{workspaceState && <small className="workspace-state"><span className={`session-dot ${workspaceState === "待审批" ? "waiting" : "running"}`} aria-hidden="true" />{workspaceState}</small>}</span>
          </button>
          <button className="workspace-action icon-button" aria-label={`在${workspaceName(workspace.workspace)}中创建会话`} title="新建会话" onClick={() => onCreateSession(workspace.workspace)}><Plus size={16} aria-hidden="true" /></button>
        </div>
        {expanded && <div className="session-list">{workspace.sessions.map((session) => {
          const indicator = indicators[session.session_id];
          const state = indicator?.approvalCount ? { label: "等待审批", tone: "waiting" } : indicator?.running ? { label: "运行中", tone: "running" }
            : indicator?.failed ? { label: "运行失败", tone: "failed" } : indicator?.cancelled ? { label: "已暂停", tone: "cancelled" }
            : indicator?.unread ? { label: "未读", tone: "unread" } : null;
          return <div className={`session-item ${session.session_id === activeId ? "active" : ""}`} key={session.session_id}>
            <button className="session-select" aria-label={`打开会话 ${session.name}`} aria-current={session.session_id === activeId ? "page" : undefined}
              onClick={() => onActivateSession(workspace.workspace, session.session_id)} title={`${session.name} · ${session.message_count} 条消息`}>
              <ChatCircle size={16} aria-hidden="true" />
              <span className="session-copy"><strong>{session.name}</strong>{state && <small className="session-status"><span className={`session-dot ${state.tone}`} aria-hidden="true" />{state.label}</small>}</span>
            </button>
            <div className="session-actions">
              <button className="session-rename icon-button" aria-label={`重命名${session.name}`} title="重命名会话" onClick={() => onRenameSession(session.session_id, session.name)}><PencilSimple size={14} aria-hidden="true" /></button>
              <button className="session-delete icon-button" disabled={indicator?.running} title={indicator?.running ? "运行结束后可删除" : "删除会话"} aria-label={`删除${session.name}`} onClick={() => onDeleteSession(session.session_id, session.name)}><Trash size={14} aria-hidden="true" /></button>
            </div>
          </div>;
        })}</div>}
      </div>; })}
      {!workspaces.length && <p className="empty-workspaces">尚无工作区</p>}
    </nav>
    <footer className="sidebar-footer"><button className="settings-entry" onClick={onOpenSettings}><GearSix size={19} aria-hidden="true" />设置</button></footer>
  </aside>;
}
