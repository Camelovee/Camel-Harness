import { useEffect, useState } from "react";

import type { RunSummary } from "../../shared/model";
import { fetchRunSummaries } from "./api";

/** 读取当前分支的 Run 摘要；Run 结束后刷新，选择项由面板回退到最新一条。 */
export function useRunLedger(
  sessionId: string | null,
  running: boolean,
  onError: (message: string) => void,
) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  useEffect(() => {
    setSelectedRunId(null);
    if (!sessionId) {
      setRuns([]);
      return;
    }
    let active = true;
    void fetchRunSummaries(sessionId)
      .then((items) => {
        if (active) setRuns(items);
      })
      .catch((reason: unknown) => {
        if (active) onError(reason instanceof Error ? reason.message : "读取运行账本失败");
      });
    return () => {
      active = false;
    };
  }, [sessionId, running, onError]);

  return { runs, selectedRunId, selectRun: setSelectedRunId };
}
