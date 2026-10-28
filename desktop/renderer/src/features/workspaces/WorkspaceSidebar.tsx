import { ChatCircle, FolderOpen, GearSix, PencilSimple, Plus, ShieldCheck, Trash, SpinnerGap } from "@phosphor-icons/react";
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
  return <aside className="sidebar" aria-label="工作区与会话">
    <div className="sidebar-heading"><span className="eyebrow">工作空间</span><span className="count-badge">{workspaces.length}</span></div>
    <button aria-label="新对话" className="new-chat" onClick={onNewChat} disabled={newChatDisabled}><Plus size={18} aria-hidden="true" />新对话<span className="shortcut-key" aria-hidden="true">⌘ N / Ctrl N</span></button>
    <button className="new-workspace" disabled={selectingWorkspace} onClick={onSelectWorkspace}>
      {selectingWorkspace ? <SpinnerGap className="spin" size={18} aria-hidden="true" /> : <FolderOpen size={18} aria-hidden="true" />}
      {selectingWorkspace ? "正在选择…" : "选择本地工作区"}
    </button>
    <div className="workspace-heading"><span>工作区与会话</span><span className="sidebar-section-rule" /></div>
    <nav className="workspace-list" aria-label="工作区导航">
      {workspaces.map((workspace) => <div className={`workspace-group ${workspace.workspace === activeWorkspace ? "active" : ""}`} key={workspace.workspace}>
        <div className="workspace-row">
          <button className="workspace-select" aria-label={`打开工作区 ${workspaceName(workspace.workspace)}`} aria-expanded={workspace.workspace === activeWorkspace} onClick={() => onActivateWorkspace(workspace)} title={workspace.workspace}>
            <span className="workspace-folder"><FolderOpen size={19} aria-hidden="true" /></span>
            <span className="workspace-copy"><strong>{workspaceName(workspace.workspace)}</strong><small>{workspace.sessions.length} 个会话{workspace.sessions.some((session) => indicators[session.session_id]?.running) && " · 运行中"}</small></span>
          </button>
          <button className="workspace-action icon-button" aria-label={`在${workspaceName(workspace.workspace)}中创建会话`} title="新建会话" onClick={() => onCreateSession(workspace.workspace)}><Plus size={16} aria-hidden="true" /></button>
        </div>
        {workspace.workspace === activeWorkspace && <div className="session-list">{workspace.sessions.map((session) => {
          const indicator = indicators[session.session_id];
          const label = indicator?.approvalCount ? "等待审批" : indicator?.running ? "运行中" : indicator?.failed ? "运行失败"
            : indicator?.cancelled ? "已暂停" : indicator?.unread ? "已完成 · 未读" : `${session.message_count} 条消息`;
          return <div className={`session-item ${session.session_id === activeId ? "active" : ""}`} key={session.session_id}>
            <button className="session-select" aria-label={`打开会话 ${session.name}`} aria-current={session.session_id === activeId ? "page" : undefined}
              onClick={() => onActivateSession(workspace.workspace, session.session_id)} title={session.name}>
              <ChatCircle size={16} aria-hidden="true" />
              <span className="session-copy"><strong>{session.name}</strong><small className="session-status"><span className={`session-dot ${indicator?.approvalCount ? "waiting" : indicator?.running ? "running" : indicator?.unread ? "unread" : ""}`} aria-hidden="true" />{label}</small></span>
            </button>
            <div className="session-actions">
              <button className="session-rename icon-button" aria-label={`重命名${session.name}`} title="重命名会话" onClick={() => onRenameSession(session.session_id, session.name)}><PencilSimple size={14} aria-hidden="true" /></button>
              <button className="session-delete icon-button" disabled={indicator?.running} title={indicator?.running ? "运行结束后可删除" : "删除会话"} aria-label={`删除${session.name}`} onClick={() => onDeleteSession(session.session_id, session.name)}><Trash size={14} aria-hidden="true" /></button>
            </div>
          </div>;
        })}</div>}
      </div>)}
      {!workspaces.length && <div className="empty-workspaces"><FolderOpen size={28} aria-hidden="true" /><strong>为想法找个落脚点</strong><p>选择本地项目，<br />你的会话会按工作区整理。</p></div>}
    </nav>
    <div className="sidebar-local-note"><ShieldCheck size={16} aria-hidden="true" /><span>会话历史保存在本机</span></div>
    <footer className="sidebar-footer"><button className="settings-entry" onClick={onOpenSettings}><GearSix size={19} aria-hidden="true" />设置</button><span className="sidebar-note">CAMEL / DESKTOP</span></footer>
  </aside>;
}
