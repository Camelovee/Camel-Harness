import { useState } from "react";

export function RenameSessionDialog({
  name,
  onCancel,
  onConfirm,
}: {
  name: string;
  onCancel: () => void;
  onConfirm: (name: string) => Promise<boolean>;
}) {
  const [value, setValue] = useState(name);
  const [saving, setSaving] = useState(false);

  async function submit() {
    const next = value.trim();
    if (!next || saving) return;
    setSaving(true);
    try {
      if (await onConfirm(next)) onCancel();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <form className="modal-card" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <div className="modal-header">
          <div><span className="eyebrow">RENAME SESSION</span><h2>重命名会话</h2></div>
          <button type="button" className="modal-close" onClick={onCancel} aria-label="关闭">×</button>
        </div>
        <label htmlFor="session-name">会话名称</label>
        <input id="session-name" autoFocus maxLength={120} value={value} onChange={(event) => setValue(event.target.value)} />
        <div className="modal-actions">
          <button type="button" className="secondary-button" onClick={onCancel}>取消</button>
          <button type="submit" className="primary-button" disabled={!value.trim() || saving}>保存</button>
        </div>
      </form>
    </div>
  );
}

export function ErrorToast({
  message,
  onClose,
}: {
  message: string;
  onClose: () => void;
}) {
  return (
    <div className="error-toast">
      {message}
      <button onClick={onClose} aria-label="关闭错误提示">
        ×
      </button>
    </div>
  );
}

export function DeleteSessionDialog({
  name,
  onCancel,
  onConfirm,
}: {
  name: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal-card">
        <div className="modal-header">
          <div>
            <span className="eyebrow">DELETE SESSION</span>
            <h2>确认删除会话</h2>
          </div>
          <button className="modal-close" onClick={onCancel}>
            ×
          </button>
        </div>
        <p className="delete-copy">确定删除“{name}”吗？此操作不可恢复。</p>
        <div className="modal-actions">
          <button className="secondary-button" onClick={onCancel}>
            取消
          </button>
          <button className="danger-button" onClick={onConfirm}>
            删除会话
          </button>
        </div>
      </div>
    </div>
  );
}
