import { useEffect, useRef, useState } from "react";

import type { ModelInput, ModelProfile, WebSettings } from "../../shared/model";

type Draft = ModelInput & { id: string | null };

type Props = {
  settings: WebSettings;
  saving: boolean;
  onSavePreferences: (modelId: string, thinking: boolean) => Promise<boolean>;
  onSaveModel: (modelId: string | null, body: ModelInput) => Promise<boolean>;
  onDeleteModel: (modelId: string) => Promise<boolean>;
};

const newDraft = (): Draft => ({
  id: null,
  name: "",
  provider: "anthropic",
  model: "",
  base_url: "",
  api_key: "",
  clear_api_key: false,
});

export function ModelSettingsPanel({
  settings,
  saving,
  onSavePreferences,
  onSaveModel,
  onDeleteModel,
}: Props) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const editorRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (draft) editorRef.current?.scrollIntoView({ block: "nearest" });
  }, [draft?.id, Boolean(draft)]);

  function edit(profile: ModelProfile) {
    setDeleteTarget(null);
    setDraft({
      id: profile.id,
      name: profile.name,
      provider: profile.provider,
      model: profile.model,
      base_url: profile.base_url ?? "",
      api_key: "",
      clear_api_key: false,
    });
  }

  async function submitDraft(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft) return;
    const { id, ...body } = draft;
    if (await onSaveModel(id, body)) setDraft(null);
  }

  async function remove(modelId: string) {
    if (await onDeleteModel(modelId)) {
      setDeleteTarget(null);
      if (draft?.id === modelId) setDraft(null);
    }
  }

  return (
    <div>
      <div className="settings-section-heading">
        <h3>对话偏好</h3>
        <p>作用于新会话；已有会话可在输入框中单独选模型。</p>
      </div>
      <label className="settings-field">
        <span>默认模型</span>
        <select
          value={settings.default_model_id}
          disabled={saving || settings.models.length === 0}
          onChange={(event) => void onSavePreferences(event.target.value, settings.thinking)}
        >
          {settings.models.length === 0 && <option value="">请先在下方添加模型</option>}
          {settings.models.map((profile) => (
            <option key={profile.id} value={profile.id} disabled={!profile.configured}>
              {profile.name}{!profile.configured ? " · 未配置密钥" : ""}
            </option>
          ))}
        </select>
      </label>
      <div className="settings-toggle-row">
        <div>
          <strong>思考模式</strong>
          <small>请求支持的模型返回思考过程，可能增加耗时和费用。</small>
        </div>
        <button
          type="button"
          className={`settings-switch ${settings.thinking ? "active" : ""}`}
          role="switch"
          aria-checked={settings.thinking}
          aria-label="思考模式"
          disabled={saving}
          onClick={() => void onSavePreferences(settings.default_model_id, !settings.thinking)}
        ><span /></button>
      </div>

      <div className="settings-section-heading settings-models-heading">
        <div>
          <h3>模型配置</h3>
          <p>选择 Provider，填写模型 ID 和密钥。密钥不会返回到浏览器。</p>
        </div>
        <button type="button" className="settings-add" onClick={() => setDraft(newDraft())}>
          ＋ 添加模型
        </button>
      </div>
      <div className="settings-model-list">
        {settings.models.map((profile) => (
          <div className="settings-model-row" key={profile.id}>
            <span className="settings-model-badge">{profile.provider === "anthropic" ? "A" : "O"}</span>
            <div className="settings-model-info">
              <strong>{profile.name}</strong>
              <small>{profile.model} · {profile.provider === "anthropic" ? "Anthropic" : "OpenAI"}</small>
            </div>
            <span className={`settings-model-status ${profile.configured ? "ready" : ""}`}>
              {profile.configured ? "可用" : "待配置"}
            </span>
            <div className="settings-model-actions">
              <button type="button" onClick={() => edit(profile)}>编辑</button>
              {deleteTarget === profile.id ? (
                <button type="button" className="settings-confirm-delete" disabled={saving} onClick={() => void remove(profile.id)}>确认删除</button>
              ) : (
                <button type="button" onClick={() => setDeleteTarget(profile.id)}>删除</button>
              )}
            </div>
          </div>
        ))}
      </div>
      <p className="settings-footnote">自定义密钥保存在 ~/.camel/config.json 中，文件权限为 0600。编辑模型将在该会话的下一轮运行生效，无需重启后端。</p>

      {draft && (
        <form ref={editorRef} className="settings-editor" onSubmit={(event) => void submitDraft(event)}>
          <div className="settings-editor-head">
            <h3>{draft.id ? "编辑模型" : "添加模型"}</h3>
            <button type="button" className="modal-close" onClick={() => setDraft(null)} aria-label="关闭模型编辑">×</button>
          </div>
          <label className="settings-field"><span>显示名称</span><input required maxLength={80} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="例如：我的 Sonnet" /></label>
          <div className="settings-form-grid">
            <label className="settings-field"><span>Provider</span><select value={draft.provider} onChange={(event) => setDraft({ ...draft, provider: event.target.value as ModelInput["provider"] })}><option value="anthropic">Anthropic</option><option value="openai">OpenAI</option></select></label>
            <label className="settings-field"><span>模型 ID</span><input required maxLength={160} value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} placeholder="模型提供商的准确 ID" /></label>
          </div>
          <label className="settings-field"><span>API Key</span><input type="password" autoComplete="new-password" value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.target.value, clear_api_key: false })} placeholder={draft.id ? "留空保留现有密钥或使用配置文件" : "留空则使用配置文件中对应 Provider 的密钥"} /></label>
          {draft.id && settings.models.find((profile) => profile.id === draft.id)?.has_api_key && (
            <label className="settings-clear-key"><input type="checkbox" checked={draft.clear_api_key} onChange={(event) => setDraft({ ...draft, clear_api_key: event.target.checked, api_key: "" })} />清除已保存的 API Key</label>
          )}
          <label className="settings-field"><span>自定义接口地址 <em>可选</em></span><input type="url" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} placeholder="https://api.example.com" /></label>
          <div className="settings-editor-actions">
            <button type="button" className="secondary-button" onClick={() => setDraft(null)}>取消</button>
            <button type="submit" className="primary-button" disabled={saving}>{saving ? "保存中…" : "保存模型"}</button>
          </div>
        </form>
      )}
    </div>
  );
}
