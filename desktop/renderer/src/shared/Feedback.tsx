import { useState } from "react";
import { WarningCircle, X } from "@phosphor-icons/react";
import { ModalDialog } from "./ModalDialog";

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
    <ModalDialog className="modal-card" labelledBy="rename-session-title" onClose={onCancel}>
      <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <div className="modal-header">
          <div><span className="eyebrow">RENAME SESSION</span><h2 id="rename-session-title">重命名会话</h2></div>
          <button type="button" className="modal-close icon-button" onClick={onCancel} aria-label="关闭"><X size={20} aria-hidden="true" /></button>
        </div>
        <label htmlFor="session-name">会话名称</label>
        <input id="session-name" autoFocus maxLength={120} value={value} onChange={(event) => setValue(event.target.value)} />
        <div className="modal-actions">
          <button type="button" className="secondary-button" onClick={onCancel}>取消</button>
          <button type="submit" className="primary-button" disabled={!value.trim() || saving}>保存</button>
        </div>
      </form>
    </ModalDialog>
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
    <div className="error-toast" role="alert">
      <WarningCircle size={20} aria-hidden="true" /><span>{message}</span>
      <button onClick={onClose} aria-label="关闭错误提示">
        <X size={18} aria-hidden="true" />
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
    <ModalDialog className="modal-card" labelledBy="delete-session-title" onClose={onCancel}>
      <div>
        <div className="modal-header">
          <div>
            <span className="eyebrow">DELETE SESSION</span>
            <h2 id="delete-session-title">确认删除会话</h2>
          </div>
          <button className="modal-close icon-button" onClick={onCancel} aria-label="关闭删除确认">
            <X size={20} aria-hidden="true" />
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
    </ModalDialog>
  );
}
