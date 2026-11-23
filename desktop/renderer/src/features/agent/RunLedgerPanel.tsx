import { useState } from "react";
import {
  ArrowUpRight,
  Check,
  CheckCircle,
  Clock,
  Copy,
  FileCode,
  Lightning,
  ShieldCheck,
  X,
} from "@phosphor-icons/react";

import { InfoTip } from "../../shared/InfoTip";
import type {
  Approval,
  Message,
  ModelProfile,
  RunSummary,
  SubagentView,
  TokenUsage,
} from "../../shared/model";
import { projectToolActivity } from "./executionProjection";
import type { RunState } from "./RunStatus";
import {
  buildRunReport,
  formatDuration,
  messagesAfter,
  messagesInRun,
  projectRunTools,
  redactHome,
  runStatusLabels,
  stopReasonLabels,
  type RunToolActivity,
} from "./runActivity";

const LIVE_RUN = "live";

type Props = {
  messages: Message[];
  approvals: Approval[];
  subagents: SubagentView[];
  running: boolean;
  state: RunState;
  phase: string;
  usage: TokenUsage;
  model: ModelProfile | undefined;
  workspace: string | null;
  runs: RunSummary[];
  selectedRunId: string | null;
  onSelectRun: (runId: string | null) => void;
  onOpenTool: (message: Message) => void;
  onOpenSubagent: (id: string) => void;
  onClose: () => void;
  onError: (message: string) => void;
};

const activityLabels = {
  succeeded: "已完成",
  failed: "执行失败",
  unknown: "结果未知",
  not_executed: "未执行",
  pending: "待审批",
  incomplete: "暂无结果",
};

export function RunLedgerPanel({
  messages,
  approvals,
  subagents,
  running,
  state,
  phase,
  usage,
  model,
  workspace,
  runs,
  selectedRunId,
  onSelectRun,
  onOpenTool,
  onOpenSubagent,
  onClose,
  onError,
}: Props) {
  const selected = selectedRunId === LIVE_RUN
    ? undefined
    : runs.find((run) => run.run_id === selectedRunId) ?? runs[runs.length - 1];
  const live = selectedRunId === LIVE_RUN || !selected || (running && selectedRunId === null);
  const liveMessages = messagesAfter(messages, runs[runs.length - 1]?.finished_at);

  return <aside className="detail-panel ledger-panel" aria-label="运行账本">
    <header className="inspector-heading">
      <div><h2>运行账本</h2></div>
      <button className="icon-button" onClick={onClose} aria-label="关闭运行账本" title="收起运行账本"><X aria-hidden="true" size={18} /></button>
    </header>
    {(runs.length > 0 || running) && <div className="ledger-runs" role="tablist" aria-label="运行记录">
      {running && <button type="button" role="tab" aria-selected={live} className={live ? "active" : ""}
        onClick={() => onSelectRun(LIVE_RUN)}>当前运行</button>}
      {runs.map((run) => <button type="button" role="tab" key={run.run_id}
        aria-selected={!live && run.run_id === selected?.run_id} className={!live && run.run_id === selected?.run_id ? "active" : ""}
        title={`${new Date(run.started_at).toLocaleString()} · ${runStatusLabels[run.status]}`}
        onClick={() => onSelectRun(run.run_id)}>
        <span className={`run-tab-dot ${run.status}`} aria-hidden="true" />
        {new Date(run.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
      </button>)}
    </div>}
    <div className="inspector-content">
      {live
        ? <LiveRun messages={liveMessages} approvals={approvals} subagents={subagents} running={running}
            state={state} phase={phase} usage={usage} model={model} workspace={workspace}
            onOpenTool={onOpenTool} onOpenSubagent={onOpenSubagent} />
        : selected
          ? <StoredRun run={selected} messages={messagesInRun(messages, selected)} onOpenTool={onOpenTool} onError={onError} />
          : null}
    </div>
  </aside>;
}

function LiveRun({ messages, approvals, subagents, running, state, phase, usage, model, workspace,
  onOpenTool, onOpenSubagent }: {
  messages: Message[];
  approvals: Approval[];
  subagents: SubagentView[];
  running: boolean;
  state: RunState;
  phase: string;
  usage: TokenUsage;
  model: ModelProfile | undefined;
  workspace: string | null;
  onOpenTool: (message: Message) => void;
  onOpenSubagent: (id: string) => void;
}) {
  const activity = projectToolActivity(messages, approvals);
  const files = activity.filter((item) => item.path && ["read_file", "write_file", "edit_file"].includes(item.name));
  const inputRatio = usage.input !== null && model ? usage.input / model.context_window_tokens : null;
  const percent = inputRatio === null ? null : Math.round(inputRatio * 100);
  const normalizedWorkspace = workspace?.replaceAll("\\", "/").replace(/\/$/, "");
  return <>
    {state !== "idle" && <section className="run-overview" aria-label="当前运行状态" aria-busy={running}>
      <p className="run-phase" key={phase}>{phase}</p>
    </section>}
    <UsageSection usage={usage} model={model} inputRatio={inputRatio} percent={percent} />
    {files.length > 0 && <section className="inspector-section" aria-label="文件活动">
      <SectionLabel title="文件活动" count={files.length} />
      <ul className="file-activity-list">{files.slice(-5).map((item) => {
        const normalizedPath = item.path!.replaceAll("\\", "/");
        const path = normalizedWorkspace && normalizedPath.startsWith(`${normalizedWorkspace}/`)
          ? normalizedPath.slice(normalizedWorkspace.length + 1) : normalizedPath;
        return <li key={item.callId}><button disabled={!item.result} onClick={() => item.result && onOpenTool(item.result)} title={item.path!}>
          <FileCode size={17} aria-hidden="true" /><span><strong>{path}</strong><small>{item.name === "read_file" ? "读取" : item.name === "write_file" ? "写入" : "编辑"} · {activityLabels[item.status]}</small></span>
          {item.result && <ArrowUpRight size={14} aria-hidden="true" />}
        </button></li>;
      })}</ul>
      {files.length > 5 && <p className="inspector-caption">最近 5 项 · 完整记录见对话</p>}
    </section>}
    {activity.length > 0 && <section className="inspector-section" aria-label="工具活动">
      <SectionLabel title="工具活动" count={activity.length} />
      <ol className="activity-timeline">{activity.slice(-4).map((item) => <li key={item.callId} data-status={item.status}>
        {item.status === "succeeded" ? <CheckCircle size={17} aria-hidden="true" /> : <Clock size={17} aria-hidden="true" />}
        <div><strong>{item.name}</strong><span>{activityLabels[item.status]}</span></div>
      </li>)}</ol>
      {activity.length > 4 && <p className="inspector-caption">最近 4 项 · 完整记录见对话</p>}
    </section>}
    {subagents.length > 0 && <section className="inspector-section" aria-label="子任务概览"><SectionLabel title="协作中的子任务" count={subagents.length} />
      <div className="inspector-subagents">{subagents.map((child) => <button key={child.session_id} title={child.task} onClick={() => onOpenSubagent(child.session_id)}>
        <Lightning size={18} aria-hidden="true" /><span><strong>{child.agent_type}</strong><small>{child.task}</small></span><ArrowUpRight size={14} aria-hidden="true" />
      </button>)}</div>
    </section>}
    {state === "idle" && !activity.length && <p className="ledger-empty">尚无运行记录</p>}
  </>;
}

function UsageSection({ usage, model, inputRatio, percent }: {
  usage: TokenUsage;
  model: ModelProfile | undefined;
  inputRatio: number | null;
  percent: number | null;
}) {
  return <section className="inspector-section usage-section" aria-label="Token 用量">
    <div className="section-label"><div className="section-title"><h3>上下文输入</h3>
      <InfoTip label="上下文输入说明">最近一次请求的输入占比，不含输出预算。</InfoTip>
    </div><span className="mono">{percent === null ? "—" : `${percent}%`}</span></div>
    <div className="usage-track" role={percent === null ? undefined : "progressbar"} aria-label="最近请求输入占模型窗口比例"
      aria-valuenow={percent === null ? undefined : Math.min(percent, 100)} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ transform: `scaleX(${inputRatio === null ? 0 : Math.min(inputRatio, 1)})` }} />
    </div>
    <p className="inspector-caption mono">{usage.input?.toLocaleString() ?? "—"} / {model?.context_window_tokens?.toLocaleString() ?? "—"} tokens</p>
    <dl className="usage-summary"><div><dt>最近输出</dt><dd>{usage.output?.toLocaleString() ?? "—"}</dd></div>
      <div><dt>本轮总量</dt><dd>{usage.runTotal?.toLocaleString() ?? "—"}</dd></div></dl>
  </section>;
}

function SectionLabel({ title, count }: { title: string; count: number }) {
  return <div className="section-label"><h3>{title}</h3><span className="count-badge">{count}</span></div>;
}

function StoredRun({ run, messages, onOpenTool, onError }: {
  run: RunSummary;
  messages: Message[];
  onOpenTool: (message: Message) => void;
  onError: (message: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const tools = projectRunTools(messages);
  const approvals = run.approvals.map((approval) => ({ kind: "approval" as const, time: approval.decided_at, approval }));
  const toolRows = tools.map((tool) => ({ kind: "tool" as const, time: tool.timestamp, tool }));
  const timeline = [...approvals, ...toolRows].sort((left, right) => left.time.localeCompare(right.time));

  async function copyReport() {
    try {
      await navigator.clipboard.writeText(buildRunReport(run, messages));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      onError("复制结案报告失败");
    }
  }

  return <>
    <section className="run-overview run-summary" aria-label="运行摘要">
      <div className="run-summary-head">
        <span className={`run-state ${run.status}`}>{runStatusLabels[run.status]}</span>
        <button type="button" className="secondary-button run-copy" onClick={() => void copyReport()}>
          {copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
          {copied ? "已复制" : "复制报告"}
        </button>
      </div>
      <p className="run-phase">{run.stop_reason ? stopReasonLabels[run.stop_reason] ?? run.stop_reason : "无停止原因"}</p>
      <dl className="run-facts">
        <div><dt>模型</dt><dd>{[run.provider, run.model].filter(Boolean).join(" / ") || "未知"}</dd></div>
        <div><dt>时长</dt><dd>{formatDuration(run)}</dd></div>
        <div><dt>用量</dt><dd>{run.total_tokens.toLocaleString()} tokens</dd></div>
        <div><dt>审批</dt><dd>{run.approvals.filter((item) => item.approved).length} 允许 / {run.approvals.filter((item) => !item.approved).length} 拒绝</dd></div>
        <div><dt>工具失败</dt><dd>{run.tool_failures}</dd></div>
      </dl>
    </section>

    {run.files.length > 0 && <section className="inspector-section" aria-label="文件变更">
      <SectionLabel title="文件变更" count={run.files.length} />
      <ul className="run-file-list">{run.files.map((path) => {
        const tool = [...tools].reverse().find((item) => item.path === path);
        const lines = tool ? diffLines(tool) : null;
        return <li key={path}><details>
          <summary><FileCode size={16} aria-hidden="true" /><code>{redactHome(path)}</code></summary>
          {lines?.length
            ? <pre className="run-diff">{lines.slice(0, 240).map((line, index) => <span key={index} className={diffClass(line)}>{line}{"\n"}</span>)}</pre>
            : <p className="inspector-caption">没有可展开的 diff，可在对话中查看工具结果。</p>}
        </details></li>;
      })}</ul>
    </section>}

    <section className="inspector-section" aria-label="运行活动">
      <SectionLabel title="运行活动" count={timeline.length} />
      {timeline.length === 0 ? <p className="inspector-caption">没有可还原的活动。</p>
        : <ol className="activity-timeline run-timeline">{timeline.map((item, index) => item.kind === "approval"
          ? <li key={`approval-${item.approval.request_id}`} data-status={item.approval.approved ? "succeeded" : "failed"}>
              <ShieldCheck size={17} aria-hidden="true" />
              <div><strong>{item.approval.approved ? "允许" : "拒绝"} {item.approval.tool_name}</strong>
                <span>{formatTime(item.time)}</span></div>
            </li>
          : <li key={item.tool.key} data-status={item.tool.status}>
              {item.tool.status === "succeeded" ? <CheckCircle size={17} aria-hidden="true" /> : <Clock size={17} aria-hidden="true" />}
              <div><strong>{item.tool.name}</strong>
                <span>{item.tool.path ? `${redactHome(item.tool.path)} · ` : ""}{activityLabels[item.tool.status] ?? item.tool.status}</span>
                {item.tool.result && <button type="button" className="run-open-tool" onClick={() => onOpenTool(item.tool.result!)}>查看结果</button>}
              </div>
            </li>)}
      </ol>}
    </section>

    {run.subagent_ids.length > 0 && <section className="inspector-section" aria-label="子任务">
      <SectionLabel title="子任务" count={run.subagent_ids.length} />
      <div className="inspector-subagents">{run.subagent_ids.map((id) => <div key={id} className="ledger-subagent">
        <Lightning size={17} aria-hidden="true" /><span>{id}</span>
      </div>)}</div>
    </section>}
  </>;
}

function diffLines(tool: RunToolActivity): string[] | null {
  if (tool.status !== "succeeded") return null;
  if (tool.name === "write_file") {
    const content = tool.arguments.content;
    return typeof content === "string" ? content.replace(/\n$/, "").split("\n").map((line) => `+${line}`) : null;
  }
  if (tool.name === "edit_file") {
    const text = tool.result?.content[0]?.content ?? "";
    return text ? text.split("\n").slice(1) : null;
  }
  return null;
}

function diffClass(line: string): string {
  if (line.startsWith("+")) return "diff-add";
  if (line.startsWith("-")) return "diff-remove";
  if (line.startsWith("@@")) return "diff-hunk";
  return "";
}

function formatTime(value: string): string {
  return value ? new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}
