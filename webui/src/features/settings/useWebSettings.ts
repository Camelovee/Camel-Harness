import { useCallback, useEffect, useState } from "react";

import type { ModelInput, WebSettings } from "../../shared/model";
import { errorText } from "../../shared/presentation";
import { addModel, deleteModel, fetchSettings, savePreferences, updateModel } from "./api";

export function useWebSettings(onError: (message: string) => void) {
  const [settings, setSettings] = useState<WebSettings | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void fetchSettings().then(setSettings).catch((reason: unknown) =>
      onError(errorText(reason, "加载设置失败")),
    );
  }, [onError]);

  const apply = useCallback(
    async (operation: () => Promise<WebSettings>): Promise<boolean> => {
      setSaving(true);
      try {
        setSettings(await operation());
        return true;
      } catch (reason) {
        onError(errorText(reason, "保存设置失败"));
        return false;
      } finally {
        setSaving(false);
      }
    },
    [onError],
  );

  return {
    settings,
    saving,
    savePreferences: (modelId: string, thinking: boolean) =>
      apply(() => savePreferences(modelId, thinking)),
    saveModel: (modelId: string | null, body: ModelInput) =>
      apply(() => modelId ? updateModel(modelId, body) : addModel(body)),
    deleteModel: (modelId: string) => apply(() => deleteModel(modelId)),
  };
}
