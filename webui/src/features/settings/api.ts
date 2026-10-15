import { apiJson } from "../../shared/api";
import type { ModelInput, WebSettings } from "../../shared/model";

export function fetchSettings(): Promise<WebSettings> {
  return apiJson<WebSettings>("/settings");
}

export function savePreferences(defaultModelId: string): Promise<WebSettings> {
  return apiJson<WebSettings>("/settings/preferences", {
    method: "PUT",
    body: JSON.stringify({ default_model_id: defaultModelId }),
  });
}

export function addModel(body: ModelInput): Promise<WebSettings> {
  return apiJson<WebSettings>("/settings/models", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateModel(modelId: string, body: ModelInput): Promise<WebSettings> {
  return apiJson<WebSettings>(`/settings/models/${modelId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteModel(modelId: string): Promise<WebSettings> {
  await apiJson<void>(`/settings/models/${modelId}`, { method: "DELETE" });
  return fetchSettings();
}
