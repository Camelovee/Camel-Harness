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
