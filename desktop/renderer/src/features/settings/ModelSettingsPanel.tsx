import { useEffect, useRef, useState } from "react";
import { Plus, X } from "@phosphor-icons/react";

import type { ModelInput, ModelProfile, WebSettings } from "../../shared/model";
import { InfoTip } from "../../shared/InfoTip";

type Draft = ModelInput & { id: string | null };

type Props = {
  settings: WebSettings;
  saving: boolean;
  onSavePreferences: (modelId: string) => Promise<boolean>;
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
  context_window_tokens: 128000,
  max_output_tokens: 16384,
  thinking_mode: "provider_default",
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
    if (draft) {
      editorRef.current?.scrollIntoView({ block: "nearest" });
      editorRef.current?.querySelector("input")?.focus();
    }
  }, [draft?.id, Boolean(draft)]);

  function edit(profile: ModelProfile) {
    setDeleteTarget(null);
    setDraft({
      id: profile.id,
      name: profile.name,
      provider: profile.provider,
      model: profile.model,
      base_url: profile.base_url ?? "",
      context_window_tokens: profile.context_window_tokens,
      max_output_tokens: profile.max_output_tokens,
      thinking_mode: profile.thinking_mode,
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
      <div className="settings-field">
        <div className="settings-field-heading"><label htmlFor="default-model">默认模型</label>
          <InfoTip label="默认模型说明">用于新会话，已有会话可在输入框中选择模型。</InfoTip>
        </div>
        <select
          id="default-model"
          value={settings.default_model_id}
          disabled={saving || settings.models.length === 0}
          onChange={(event) => void onSavePreferences(event.target.value)}
        >
          {settings.models.length === 0 && <option value="">请先在下方添加模型</option>}
          {settings.models.map((profile) => (
            <option key={profile.id} value={profile.id} disabled={!profile.configured}>
              {profile.name}{!profile.configured ? " · 未配置密钥" : ""}
            </option>
          ))}
        </select>
      </div>

      <div className="settings-section-heading settings-models-heading">
        <h3>模型列表</h3>
        <button type="button" className="settings-add" onClick={() => { setDeleteTarget(null); setDraft(newDraft()); }}>
          <Plus size={16} aria-hidden="true" />添加模型
        </button>
      </div>
      <div className="settings-model-list">
        {settings.models.map((profile) => (
          <div className="settings-model-entry" key={profile.id}>
            <div className="settings-model-row">
              <div className="settings-model-info">
                <strong>{profile.name}</strong>
                <small>{profile.model} · {profile.provider === "anthropic" ? "Anthropic" : "OpenAI"}</small>
              </div>
              <span className={`settings-model-status ${profile.configured ? "ready" : ""}`}>
                {profile.configured ? "可用" : "待配置"}
              </span>
              <div className="settings-model-actions">
                <button type="button" aria-label={`编辑模型 ${profile.name}`} onClick={() => edit(profile)}>编辑</button>
                <button type="button" aria-label={`删除模型 ${profile.name}`} aria-expanded={deleteTarget === profile.id}
                  onClick={() => setDeleteTarget(deleteTarget === profile.id ? null : profile.id)}>删除</button>
              </div>
            </div>
            {deleteTarget === profile.id && <div className="settings-delete-confirmation" role="group" aria-label={`删除模型 ${profile.name}`}>
              <p>删除“{profile.name}”的配置无法撤销，相关会话将切换到默认模型。</p>
              <div><button type="button" className="secondary-button" disabled={saving} onClick={() => setDeleteTarget(null)}>取消</button>
                <button type="button" className="danger-button" disabled={saving} onClick={() => void remove(profile.id)}>确认删除</button></div>
            </div>}
          </div>
        ))}
      </div>

      {draft && (
        <form key={draft.id ?? "new"} ref={editorRef} className="settings-editor" onSubmit={(event) => void submitDraft(event)}>
          <div className="settings-editor-head">
            <h3>{draft.id ? "编辑模型" : "添加模型"}</h3>
            <button type="button" className="modal-close icon-button" onClick={() => setDraft(null)} aria-label="关闭模型编辑"><X size={18} aria-hidden="true" /></button>
          </div>
          <label className="settings-field"><span>显示名称</span><input required maxLength={80} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
          <div className="settings-form-grid">
            <label className="settings-field"><span>服务商</span><select value={draft.provider} onChange={(event) => setDraft({ ...draft, provider: event.target.value as ModelInput["provider"], thinking_mode: "provider_default" })}><option value="anthropic">Anthropic</option><option value="openai">OpenAI</option></select></label>
            <label className="settings-field"><span>模型 ID</span><input required maxLength={160} value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} placeholder="模型提供商的准确 ID" /></label>
          </div>
          <div className="settings-field">
            <div className="settings-field-heading"><label htmlFor="model-api-key">API Key</label>
              <InfoTip label="密钥保存说明">密钥保存在本机 ~/.camel/config.json，文件权限为 0600，保存后不再回显。模型修改在下一轮运行生效。</InfoTip>
            </div>
            <input id="model-api-key" type="password" autoComplete="new-password" value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.target.value, clear_api_key: false })} placeholder={draft.id ? "留空保留现有密钥" : "留空使用服务商配置中的密钥"} />
          </div>
          {draft.id && settings.models.find((profile) => profile.id === draft.id)?.has_api_key && (
            <label className="settings-clear-key"><input type="checkbox" checked={draft.clear_api_key} onChange={(event) => setDraft({ ...draft, clear_api_key: event.target.checked, api_key: "" })} />清除已保存的 API Key</label>
          )}
          <details className="settings-advanced" onInvalid={(event) => { event.currentTarget.open = true; }}>
            <summary>高级参数</summary>
            <div className="settings-form-grid">
              <label className="settings-field"><span>上下文窗口 Token</span><input type="number" required min={draft.max_output_tokens + 4097} step={1} value={draft.context_window_tokens} onChange={(event) => setDraft({ ...draft, context_window_tokens: Number(event.target.value) })} /></label>
              <label className="settings-field"><span>最大输出 Token</span><input type="number" required min={1} max={draft.context_window_tokens - 4097} step={1} value={draft.max_output_tokens} onChange={(event) => setDraft({ ...draft, max_output_tokens: Number(event.target.value) })} /></label>
            </div>
            <label className="settings-field"><span>思考参数模式</span><select value={draft.thinking_mode} onChange={(event) => setDraft({ ...draft, thinking_mode: event.target.value as ModelInput["thinking_mode"] })}><option value="provider_default">服务端默认（省略参数）</option>{draft.provider === "anthropic" ? <option value="anthropic_adaptive">Anthropic adaptive</option> : <option value="openai_reasoning">OpenAI reasoning</option>}</select></label>
            <p className="settings-footnote">按模型能力设置预算，输出预算包含思考 Token。省略思考参数时使用服务端默认行为。</p>
            <label className="settings-field"><span>自定义接口地址 <em>可选</em></span><input type="url" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} placeholder="https://api.example.com" /></label>
          </details>
          <div className="settings-editor-actions">
            <button type="button" className="secondary-button" onClick={() => setDraft(null)}>取消</button>
            <button type="submit" className="primary-button" disabled={saving}>{saving ? "保存中…" : "保存模型"}</button>
          </div>
        </form>
      )}
    </div>
  );
}
