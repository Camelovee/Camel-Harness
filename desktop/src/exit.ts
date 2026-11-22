import type { BackendConnection } from "./backend";

export async function prepareExit(
  connection: BackendConnection,
  askToStop: (activeRuns: number | null) => Promise<boolean>,
): Promise<boolean> {
  const request = (method: "POST" | "DELETE") => fetch(`${connection.origin}/api/desktop/exit`, {
    method, headers: { "X-Camel-Desktop-Token": connection.token },
    signal: AbortSignal.timeout(2000), redirect: "error",
  });
  let activeRuns: number | null = null;
  try {
    const response = await request("POST");
    if (response.ok) {
      const status: unknown = await response.json();
      if (typeof status === "object" && status !== null && "active_runs" in status &&
          typeof status.active_runs === "number" && Number.isInteger(status.active_runs) && status.active_runs >= 0) {
        activeRuns = status.active_runs;
      }
    }
  } catch { /* 状态未知时仍由用户决定是否停止。 */ }
  if (activeRuns === 0 || await askToStop(activeRuns)) return true;
  const response = await request("DELETE");
  if (!response.ok) throw new Error("无法恢复任务入口，请重新启动应用");
  return false;
}
