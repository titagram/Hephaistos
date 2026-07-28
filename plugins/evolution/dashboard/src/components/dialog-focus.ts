import { React, SDK } from "../sdk";

void React;

export interface DialogFocusRef {
  current: HTMLElement | null;
}

export interface DialogFocusOptions {
  dialogRef: DialogFocusRef;
  onClose(): void;
  returnFocusRef?: DialogFocusRef;
}

function focusableElements(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )].filter(element => element.getAttribute("aria-hidden") !== "true");
}

export function useDialogFocus({ dialogRef, onClose, returnFocusRef }: DialogFocusOptions): {
  close(): void;
  handleKeyDown(event: React.KeyboardEvent<HTMLElement>): void;
} {
  const { useEffect } = SDK.hooks;

  const restoreFocus = () => {
    const trigger = returnFocusRef?.current;
    if (trigger !== null && trigger !== undefined && document.contains(trigger)) trigger.focus();
  };

  const close = () => {
    onClose();
    restoreFocus();
  };

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog === null) return;
    focusableElements(dialog)[0]?.focus();
  }, []);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;

    const dialog = dialogRef.current;
    if (dialog === null) return;
    const elements = focusableElements(dialog);
    if (elements.length === 0) {
      event.preventDefault();
      dialog.focus();
      return;
    }
    const first = elements[0]!;
    const last = elements.at(-1)!;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return { close, handleKeyDown };
}
