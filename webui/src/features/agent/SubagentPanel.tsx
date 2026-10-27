import type { Approval, SubagentView } from "../../shared/model";
import { MessageList } from "./MessageList";

export function SubagentPanel({ child, workspace, resolvingApprovalId, onResolveApproval, onClose }: {
  child: SubagentView;
  workspace: string | null;
  resolvingApprovalId: string | null;
  onResolveApproval: (requestId: string, approved: boolean) => void;
  onClose: () => void;
}) {
  const known = new Set(child.messages.flatMap((message) => message.approval ? [message.approval.request_id] : []));
  const pending: Approval[] = child.approvals.filter((approval) => !known.has(approval.request_id));
  const messages = [...child.messages, ...pending.map((approval) => ({
    id: `approval-${approval.request_id}`, role: "approval" as const, content: [], approval,
  }))];
  const reasonLabels: Record<string, string> = {
    model_completed: "任务完成", max_turns: "达到轮数上限", model_length: "模型输出被截断",
    provider_failed: "模型服务失败", session_failed: "会话保存失败", context_failed: "上下文处理失败",
    resource_failed: "资源加载失败", tool_failed: "工具或结果保存失败", internal_error: "内部错误",
    cancelled: "运行已取消", timeout: "运行超时",
  };
  const running = child.status === "running" || child.status === "waiting_approval";
  return <aside className="subagent-panel" aria-label="子任务详情">
    <header className="subagent-panel-head">
      <div><strong>{child.agent_type} 子任务</strong><p>{child.task}</p></div>
      <button aria-label="关闭子任务详情" onClick={onClose}>×</button>
    </header>
    <div className="subagent-phase" role="status">{child.phase}{child.turn > 0 ? ` · 第 ${child.turn} 轮` : ""}</div>
    {child.loading && <p className="subagent-notice">正在加载历史…</p>}
    {child.error && <p className="subagent-notice error">{child.error}</p>}
    <MessageList activeSession={{ session_id: child.session_id, name: "子任务", workspace: workspace ?? "",
      updated_at: "", message_count: messages.length, archived: false, model_id: null }}
      activeWorkspace={workspace} messages={messages} running={running}
      streamAssistantId={child.streamAssistantId} resolvingApprovalId={resolvingApprovalId}
      onResolveApproval={onResolveApproval} onStart={() => undefined} showToolCalls />
    {child.task_outcome && <footer className="subagent-outcome">
      <strong>{({ completed: "已完成", failed: "执行失败", cancelled: "已取消", unknown: "结果未知" })[child.task_outcome.status]}</strong>
      {child.task_outcome.stop_reason && <span> · {reasonLabels[child.task_outcome.stop_reason] ?? child.task_outcome.stop_reason}</span>}
      {child.task_outcome.error && <p>{child.task_outcome.error}</p>}
      {child.task_outcome.finished_at && <small>{new Date(child.task_outcome.finished_at).toLocaleString()}</small>}
    </footer>}
  </aside>;
}
