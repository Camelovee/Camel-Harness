import { ArrowUpRight, CheckCircle, Clock, FileCode, FolderOpen, Lightning, ShieldCheck, X } from "@phosphor-icons/react";
import type { Approval, Message, ModelProfile, PermissionMode, SubagentView, ThinkingEffort, TokenUsage } from "../../shared/model";
import { workspaceName } from "../../shared/presentation";
import { projectToolActivity } from "./executionProjection";
import { RunStatus, type RunState } from "./RunStatus";

type Props = {
  messages: Message[];
  approvals: Approval[];
  subagents: SubagentView[];
  running: boolean;
  state: RunState;
  phase: string;
  usage: TokenUsage;
  model: ModelProfile | undefined;
  permissionMode: PermissionMode;
  effort: ThinkingEffort;
  workspace: string | null;
  onOpenTool: (message: Message) => void;
  onOpenSubagent: (id: string) => void;
  onClose: () => void;
};

const activityLabels = {
  succeeded: "已完成", failed: "执行失败", unknown: "结果未知", not_executed: "未执行",
  pending: "待审批", incomplete: "暂无结果",
};

export function ExecutionPanel({ messages, approvals, subagents, running, state, phase, usage, model,
  permissionMode, effort, workspace, onOpenTool, onOpenSubagent, onClose }: Props) {
  const activity = projectToolActivity(messages, approvals);
  const files = activity.filter((item) => item.path && ["read_file", "write_file", "edit_file"].includes(item.name));
  const inputRatio = usage.input !== null && model ? usage.input / model.context_window_tokens : null;
  const percent = inputRatio === null ? null : Math.round(inputRatio * 100);
  const normalizedWorkspace = workspace?.replaceAll("\\", "/").replace(/\/$/, "");

  return <aside className="detail-panel execution-panel" aria-label="执行详情">
    <header className="inspector-heading">
      <div><h2>执行详情</h2></div>
      <button className="icon-button" onClick={onClose} aria-label="关闭执行详情" title="收起执行详情"><X aria-hidden="true" size={18} /></button>
    </header>
    <div className="inspector-content">
      <section className="run-overview" aria-label="当前运行状态" aria-busy={running}>
        <div className="section-label"><span>当前任务</span><RunStatus state={state} /></div>
        <p className="run-phase" key={phase}>{phase}</p>
      </section>

      <section className="inspector-section usage-section" aria-label="Token 用量">
        <div className="section-label"><h3>上下文输入</h3><span className="mono">{percent === null ? "—" : `${percent}%`}</span></div>
        <div className="usage-track" role={percent === null ? undefined : "progressbar"} aria-label="最近请求输入占模型窗口比例"
          aria-valuenow={percent === null ? undefined : Math.min(percent, 100)} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ transform: `scaleX(${inputRatio === null ? 0 : Math.min(inputRatio, 1)})` }} />
        </div>
        <p className="inspector-caption mono">{usage.input?.toLocaleString() ?? "—"} / {model?.context_window_tokens?.toLocaleString() ?? "—"} tokens</p>
        <p className="usage-explanation">最近一次请求的输入占比，不含输出预算。</p>
        <dl className="usage-summary"><div><dt>最近输出</dt><dd>{usage.output?.toLocaleString() ?? "—"}</dd></div>
          <div><dt>本轮总量</dt><dd>{usage.runTotal?.toLocaleString() ?? "—"}</dd></div></dl>
      </section>

      <section className="inspector-section" aria-label="文件活动">
        <div className="section-label"><h3>文件活动</h3><span className="count-badge">{files.length}</span></div>
        {files.length ? <ul className="file-activity-list">{files.slice(-5).map((item) => {
          const normalizedPath = item.path!.replaceAll("\\", "/");
          const path = normalizedWorkspace && normalizedPath.startsWith(`${normalizedWorkspace}/`)
            ? normalizedPath.slice(normalizedWorkspace.length + 1) : normalizedPath;
          return <li key={item.callId}><button disabled={!item.result} onClick={() => item.result && onOpenTool(item.result)} title={item.path!}>
            <FileCode size={17} aria-hidden="true" /><span><strong>{path}</strong><small>{item.name === "read_file" ? "读取" : item.name === "write_file" ? "写入" : "编辑"} · {activityLabels[item.status]}</small></span>
            {item.result && <ArrowUpRight size={14} aria-hidden="true" />}
          </button></li>;
        })}</ul> : <div className="inspector-empty"><FileCode size={22} aria-hidden="true" /><p>尚无文件操作<span>读取与修改记录会显示在这里</span></p></div>}
        {files.length > 5 && <p className="inspector-caption">最近 5 次操作 · 完整记录见对话</p>}
      </section>

      <section className="inspector-section" aria-label="工具活动">
        <div className="section-label"><h3>工具活动</h3><span className="count-badge">{activity.length}</span></div>
        {activity.length ? <ol className="activity-timeline">{activity.slice(-4).map((item) => <li key={item.callId} data-status={item.status}>
          {item.status === "succeeded" ? <CheckCircle size={17} aria-hidden="true" /> : <Clock size={17} aria-hidden="true" />}
          <div><strong>{item.name}</strong><span>{activityLabels[item.status]}</span></div>
        </li>)}</ol> : <p className="inspector-caption">开始任务后，在这里查看执行轨迹。</p>}
        {activity.length > 4 && <p className="inspector-caption">最近 4 项活动 · 完整记录见对话</p>}
      </section>

      {subagents.length > 0 && <section className="inspector-section" aria-label="子任务概览"><div className="section-label"><h3>协作中的子任务</h3><span className="count-badge">{subagents.length}</span></div>
        <div className="inspector-subagents">{subagents.map((child) => <button key={child.session_id} title={child.task} onClick={() => onOpenSubagent(child.session_id)}>
          <Lightning size={18} aria-hidden="true" /><span><strong>{child.agent_type}</strong><small>{child.task}</small></span><ArrowUpRight size={14} aria-hidden="true" />
        </button>)}</div>
      </section>}

      <section className="inspector-section execution-config" aria-label="运行配置"><h3>运行配置</h3>
        <dl><div><dt>模型</dt><dd title={model?.model}>{model?.name ?? "尚未配置"}</dd></div>
          <div><dt>思考</dt><dd>{({ low: "低", high: "中", max: "高" })[effort]}</dd></div>
          <div><dt>权限</dt><dd>{({ default: "默认审批", plan: "只读计划", yolo: "自动允许" })[permissionMode]}</dd></div></dl>
        <div className="inspector-workspace" title={workspace ?? ""}><FolderOpen size={16} aria-hidden="true" /><span>{workspace ? workspaceName(workspace) : "选择工作区"}</span></div>
      </section>
    </div>
    <footer className="inspector-footer"><ShieldCheck size={16} aria-hidden="true" />操作由权限规则控制</footer>
  </aside>;
}
