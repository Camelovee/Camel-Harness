import { useEffect, useState } from "react";

import { errorText } from "../../shared/presentation";
import { fetchFileConfig, saveFileConfig, type FileConfig } from "./api";

export function DefaultModelEditor() {
  const [config, setConfig] = useState<FileConfig | null>(null);
  const [provider, setProvider] = useState<FileConfig["provider"]>("anthropic");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  function accept(value: FileConfig) {
    setConfig(value);
    setProvider(value.provider);
    setModel(value.model);
    setBaseUrl(value.base_url ?? "");
    setApiKey("");
    setClearKey(false);
  }

  async function reload() {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      accept(await fetchFileConfig());
    } catch (reason) {
      setError(errorText(reason, "加载默认模型失败"));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void reload(); }, []);

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!config) return;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      accept(await saveFileConfig({
        provider, model, base_url: baseUrl || null, api_key: apiKey,
        clear_api_key: clearKey, revision: config.revision,
      }));
      setSaved(true);
    } catch (reason) {
      setError(errorText(reason, "保存默认模型失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings-config" aria-labelledby="default-model-heading">
      <h3 id="default-model-heading">配置文件默认模型</h3>
      <p className="settings-footnote">保存到 {config?.path ?? "~/.camel/config.json"}，供 CLI 和后端使用。</p>
      <form onSubmit={(event) => void save(event)} onChange={() => setSaved(false)}>
        <fieldset disabled={busy || !config} className="settings-config-fields">
          <div className="settings-form-grid">
            <label className="settings-field"><span>Provider</span>
              <select value={provider} onChange={(event) => {
                const selected = event.target.value as FileConfig["provider"];
                setProvider(selected);
                setModel(selected === config?.provider ? config.model : "");
                setBaseUrl(selected === config?.provider ? config.base_url ?? "" : "");
                setApiKey(""); setClearKey(false);
              }}><option value="anthropic">Anthropic</option><option value="openai">OpenAI</option></select>
            </label>
            <label className="settings-field"><span>模型 ID</span>
              <input required maxLength={160} value={model} onChange={(event) => setModel(event.target.value)} placeholder="模型提供商的准确 ID" />
            </label>
          </div>
          <label className="settings-field"><span>API Key · {config?.keys_configured[provider] ? "已配置" : "未配置"}</span>
            <input type="password" autoComplete="new-password" maxLength={4096} value={apiKey} onChange={(event) => { setApiKey(event.target.value); setClearKey(false); }} placeholder="填写新密钥；留空保留已保存密钥" />
          </label>
          {config?.keys_configured[provider] && <label className="settings-clear-key">
            <input type="checkbox" checked={clearKey} onChange={(event) => { setClearKey(event.target.checked); setApiKey(""); }} />清除该 Provider 已保存的 API Key
          </label>}
          <label className="settings-field"><span>自定义接口地址 <em>可选</em></span>
            <input type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="留空使用官方接口" />
          </label>
          <p className="settings-footnote">密钥不会回显。保存后重启后端生效，CLI 下次启动时读取。</p>
          <div className="settings-editor-actions">
            <button type="button" className="secondary-button" onClick={() => void reload()}>重新加载</button>
            <button type="submit" className="primary-button">{busy ? "保存中…" : "保存默认模型"}</button>
          </div>
        </fieldset>
        {error && <p role="alert">{error}</p>}
        {!config && <button type="button" className="secondary-button" disabled={busy} onClick={() => void reload()}>重新加载</button>}
        {saved && <p role="status">默认模型已保存到 config.json，请重启后端生效。</p>}
      </form>
    </section>
  );
}
