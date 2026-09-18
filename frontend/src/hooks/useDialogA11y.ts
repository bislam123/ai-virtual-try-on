import { useEffect, useRef } from "react";

/** Baseline dialog accessibility every modal in this app needs: Escape
 * closes it, focus moves into the dialog on open, and returns to
 * whatever had focus before it opened once it closes (the WAI-ARIA
 * dialog pattern) -- without a full manual tab-cycling focus trap.
 * Nothing behind an open modal here is inert/hidden, so native Tab order
 * already stays sane; a hand-rolled trap is exactly the kind of "focus
 * becomes trapped incorrectly" risk that's not worth taking on for a
 * short-lived auth/confirmation form.
 *
 * Focuses the dialog *panel* itself (the caller attaches the returned
 * ref to a `tabIndex={-1}` container), not the first input -- on a
 * mobile device, autofocusing a text input would pop the virtual
 * keyboard the instant the modal opens, which is more disruptive than
 * helpful.
 *
 * Runs its setup/teardown exactly once (mount/unmount), regardless of
 * how many times `onClose`'s identity changes across re-renders -- it's
 * read through a ref so the escape handler always calls the latest
 * version without needing to be in the effect's dependency array (which
 * would otherwise re-run the focus save/restore logic on every render).
 */
export function useDialogA11y(onClose: () => void) {
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);

  // Runs after every render (no dependency array) purely to keep the ref
  // in sync -- not the mount-effect below, and deliberately not a
  // during-render assignment, which is what oxlint's react(refs) rule
  // warns about (mutating a ref outside an effect/handler).
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (previouslyFocused && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, []);

  return panelRef;
}
