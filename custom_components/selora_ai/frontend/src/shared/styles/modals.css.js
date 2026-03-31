import { css } from "lit";

export const sharedModals = css`
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 10001;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .modal-content {
    background: var(--card-background-color, #fff);
    border-radius: 16px;
    border: 1px solid var(--selora-zinc-800);
    padding: 24px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
    width: 90%;
  }
  .modal {
    background: var(--card-background-color, #fff);
    border-radius: 16px;
    border: 1px solid var(--divider-color);
    padding: 24px;
    max-width: 420px;
    width: 90%;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  }
  .modal-title {
    font-size: 18px;
    font-weight: 700;
    margin: 0 0 16px;
  }
  .modal-label {
    font-size: 13px;
    font-weight: 500;
    display: block;
    margin-bottom: 6px;
  }
  .modal-row {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .modal-input {
    flex: 1;
    padding: 10px 12px;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font-size: 14px;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s;
  }
  .modal-input:focus {
    border-color: var(--selora-accent);
  }
  .modal-input::placeholder {
    opacity: 0.35;
  }
  .modal-input.generating-placeholder {
    display: flex;
    align-items: center;
    gap: 8px;
    border-color: var(--selora-accent);
  }
  .modal-row.generating .modal-magic-btn {
    border-color: var(--selora-accent);
  }
  .modal-magic-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 8px 10px;
    flex-shrink: 0;
    border-radius: 6px;
    border: 1.5px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    cursor: pointer;
    font-weight: 600;
    transition: opacity 0.15s;
  }
  .modal-magic-btn:hover {
    opacity: 0.85;
    border-color: var(--selora-accent);
    color: var(--selora-accent-text);
  }
  .modal-magic-btn ha-icon {
    --mdc-icon-size: 20px;
  }
  .modal-actions {
    display: flex;
    gap: 8px;
    margin-top: 16px;
    justify-content: flex-end;
  }
  .modal-btn {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 6px 14px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
    border: 1.5px solid transparent;
    background: transparent;
    transition:
      background 0.15s,
      opacity 0.15s;
    user-select: none;
    letter-spacing: normal;
  }
  .modal-btn:hover {
    opacity: 0.85;
  }
  .modal-btn ha-icon {
    --mdc-icon-size: 14px;
  }
  .modal-cancel {
    border-color: var(--divider-color);
    color: var(--primary-text-color);
    background: var(--card-background-color);
  }
  .modal-cancel:hover {
    border-color: var(--selora-accent);
    color: var(--selora-accent-text);
  }
  .modal-create {
    background: var(--selora-accent);
    border-color: var(--selora-accent);
    color: #1a1a1a;
  }
  .modal-create:hover:not(:disabled) {
    box-shadow: var(--selora-glow);
    background: var(--selora-accent-light);
  }
  .modal-create:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
`;
