interface UpdatePromptProps {
  onUpdate: () => void;
}

/** Small, unobtrusive banner (same shape/pattern as OfflineBanner) shown
 * once hooks/usePwaUpdate.ts reports a new service worker is installed
 * and waiting. Never appears automatically-then-disappears and never
 * reloads on its own -- it stays up until the user explicitly taps
 * Update, which is the only thing that ever triggers a reload (see that
 * hook's own docstring). role="status": an update becoming available is
 * exactly the kind of non-urgent-but-worth-announcing async status
 * change aria-live is for, without interrupting whatever the user is
 * doing (role="alert" would be too aggressive here). */
export default function UpdatePrompt({ onUpdate }: UpdatePromptProps) {
  return (
    <div
      role="status"
      className="flex items-center justify-between gap-3 bg-indigo-600 px-4 py-2 text-sm font-medium text-white"
      style={{ paddingTop: "calc(0.5rem + env(safe-area-inset-top, 0px))" }}
    >
      <span>A new version of Try It On is available.</span>
      <button
        type="button"
        onClick={onUpdate}
        className="min-h-11 shrink-0 rounded-full bg-white px-4 py-2 text-xs font-semibold text-indigo-700 active:scale-[0.98]"
      >
        Update
      </button>
    </div>
  );
}
