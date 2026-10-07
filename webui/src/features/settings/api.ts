import { apiJson } from "../../shared/api";
import type { ModelInput, WebSettings } from "../../shared/model";

export type FileConfig = {
  path: string;
  provider: ModelInput["provider"];
  model: string;
  base_url: string | null;
  revision: string;
  keys_configured: { anthropic: boolean; openai: boolean };
};

export function fetchFileConfig(): Promise<FileConfig> {
  return apiJson<FileConfig>("/settings/config");
}

export function saveFileConfig(body: {
  provider: ModelInput["provider"];
  model: string;
  base_url: string | null;
  api_key: string;
  clear_api_key: boolean;
  revision: string;
}): Promise<FileConfig> {
  return apiJson<FileConfig>("/settings/config", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function fetchSettings(): Promise<WebSettings> {
  return apiJson<WebSettings>("/settings");
}

export function savePreferences(defaultModelId: string, thinking: boolean): Promise<WebSettings> {
  return apiJson<WebSettings>("/settings/preferences", {
    method: "PUT",
    body: JSON.stringify({ default_model_id: defaultModelId, thinking }),
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
