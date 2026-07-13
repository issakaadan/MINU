import type { ReactNode } from "react";

type ModalProps = {
  title: string;
  children: ReactNode;
  actions: ReactNode;
  onClose: () => void;
};

export function Modal({ title, children, actions, onClose }: ModalProps) {
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-card__header">
          <h2>{title}</h2>
          <button className="button button--ghost" onClick={onClose} type="button">
            Close
          </button>
        </div>
        <div className="modal-card__body">{children}</div>
        <div className="modal-card__actions">{actions}</div>
      </div>
    </div>
  );
}
