import type { Workspace } from "../../shared/model";
import { workspaceName } from "../../shared/presentation";

type Props = {
  workspaces: Workspace[];
  activeWorkspace: string | null;
  activeId: string | null;
  selectingWorkspace: boolean;
  onSelectWorkspace: () => void;
  onActivateWorkspace: (workspace: Workspace) => void;
  onActivateSession: (workspace: string, sessionId: string) => void;
  onCreateSession: (workspace: string) => void;
  onDeleteSession: (sessionId: string, name: string) => void;
};

export function WorkspaceSidebar({
  workspaces,
  activeWorkspace,
  activeId,
  selectingWorkspace,
  onSelectWorkspace,
  onActivateWorkspace,
  onActivateSession,
  onCreateSession,
  onDeleteSession,
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
            还没有工作区
            <br />
            <span>选择一个本地文件夹开始</span>
          </div>
        )}
      </div>
      <div className="sidebar-note">工作区只保存在当前本机用户环境</div>
    </aside>
  );
}
