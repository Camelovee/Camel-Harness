export type RunState = "idle" | "running" | "waiting" | "completed" | "failed" | "cancelled";

const labels: Record<RunState, string> = {
  idle: "就绪", running: "运行中", waiting: "待审批", completed: "已完成", failed: "运行失败", cancelled: "已取消",
};

export function RunStatus({ state, announce = false }: { state: RunState; announce?: boolean }) {
  return <span className="run-status" data-state={state} role={announce ? "status" : undefined}>
    <span className="run-status-dot" aria-hidden="true" />{labels[state]}
  </span>;
}
