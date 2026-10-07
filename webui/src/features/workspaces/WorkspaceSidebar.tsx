import type { Workspace } from "../../shared/model";
import { workspaceName } from "../../shared/presentation";

type Props = {
  workspaces: Workspace[];
  activeWorkspace: string | null;
  activeId: string | null;
  selectingWorkspace: boolean;
  newChatDisabled: boolean;
  onSelectWorkspace: () => void;
  onNewChat: () => void;
  onActivateWorkspace: (workspace: Workspace) => void;
  onActivateSession: (workspace: string, sessionId: string) => void;
  onCreateSession: (workspace: string) => void;
  onDeleteSession: (sessionId: string, name: string) => void;
  onRenameSession: (sessionId: string, name: string) => void;
  onOpenSettings: () => void;
};

export function WorkspaceSidebar({
  workspaces,
  activeWorkspace,
  activeId,
  selectingWorkspace,
  newChatDisabled,
  onSelectWorkspace,
  onNewChat,
  onActivateWorkspace,
  onActivateSession,
  onCreateSession,
  onDeleteSession,
  onRenameSession,
  onOpenSettings,
}: Props) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-brand">
          <span className="brand-logo">🐫</span>
          <div className="brand-wordmark">
            <strong>camel</strong>
            <span>HARNESS</span>
            <em>Python</em>
          </div>
        </div>
      </div>
      <button className="new-chat" onClick={onNewChat} disabled={newChatDisabled}>
        <span>＋</span> 新对话
      </button>
      <button
        className="new-workspace"
        disabled={selectingWorkspace}
        onClick={onSelectWorkspace}
      >
        <span>{selectingWorkspace ? "…" : "＋"}</span>
        {selectingWorkspace ? "正在选择…" : "选择本地工作区"}
      </button>
      <div className="workspace-heading">
        <span>工作区</span>
        <span className="workspace-total">{workspaces.length}</span>
      </div>
      <div className="workspace-list">
        {workspaces.map((workspace) => (
          <div
            className={`workspace-group ${workspace.workspace === activeWorkspace ? "active" : ""}`}
            key={workspace.workspace}
          >
            <div
              className="workspace-row"
              onClick={() => onActivateWorkspace(workspace)}
              role="button"
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === "Enter") onActivateWorkspace(workspace);
              }}
            >
              <span className="folder-icon">▱</span>
              <span className="workspace-copy">
                <strong>{workspaceName(workspace.workspace)}</strong>
                <small>{workspace.sessions.length} 个会话</small>
              </span>
              <button
                className="workspace-action"
                aria-label={`在${workspaceName(workspace.workspace)}中创建会话`}
                onClick={(event) => {
                  event.stopPropagation();
                  onCreateSession(workspace.workspace);
                }}
              >
                ＋
              </button>
            </div>
            {workspace.workspace === activeWorkspace && (
              <div className="session-list">
                {workspace.sessions.map((session) => (
                  <div
                    className={`session-item ${session.session_id === activeId ? "active" : ""}`}
                    key={session.session_id}
                    onClick={() => onActivateSession(workspace.workspace, session.session_id)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        onActivateSession(workspace.workspace, session.session_id);
                      }
                    }}
                  >
                    <span className="session-dot" />
                    <span className="session-copy">
                      <strong>{session.name}</strong>
                      <small>{session.message_count} 条消息</small>
                    </span>
                    <button
                      className="session-rename"
                      aria-label={`重命名${session.name}`}
                      title="重命名会话"
                      onClick={(event) => {
                        event.stopPropagation();
                        onRenameSession(session.session_id, session.name);
                      }}
                    >
                      ✎
                    </button>
                    <button
                      className="session-delete"
                      aria-label={`删除${session.name}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        onDeleteSession(session.session_id, session.name);
                      }}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {!workspaces.length && (
          <div className="empty-workspaces">
            还没有会话
            <br />
            <span>输入消息将在用户主目录开始，或选择本地文件夹</span>
          </div>
        )}
      </div>
      <div className="sidebar-footer">
        <button type="button" className="settings-entry" onClick={onOpenSettings}>
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M9.7 2.8h4.6l.6 2.1 1.8.8 2-.9 3.2 3.2-.9 2 .8 1.8 2.1.6v4.6l-2.1.6-.8 1.8.9 2-3.2 3.2-2-.9-1.8.8-.6 2.1H9.7l-.6-2.1-1.8-.8-2 .9-3.2-3.2.9-2-.8-1.8-2.1-.6v-4.6l2.1-.6.8-1.8-.9-2 3.2-3.2 2 .9 1.8-.8z" transform="translate(0 -2) scale(.96)" />
            <circle cx="12" cy="12" r="3" />
          </svg>
          <span>设置</span>
        </button>
        <span className="sidebar-note">本机工作区</span>
      </div>
    </aside>
  );
}
