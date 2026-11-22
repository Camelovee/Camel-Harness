import { useEffect, useRef, type ReactNode } from "react";

export function ModalDialog({ children, className, labelledBy, onClose, id }: {
  children: ReactNode;
  className: string;
  labelledBy: string;
  onClose: () => void;
  id?: string;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return <dialog ref={dialogRef} id={id} className={`modal-frame ${className}`} aria-labelledby={labelledBy}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onKeyDown={(event) => event.stopPropagation()}
    onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    {children}
  </dialog>;
}
