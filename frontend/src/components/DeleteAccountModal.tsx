import { useState, type FormEvent } from "react";
import { ApiError } from "../api/http";

interface DeleteAccountModalProps {
  onClose: () => void;
  onConfirm: (password: string) => Promise<void>;
}

export default function DeleteAccountModal({ onClose, onConfirm }: DeleteAccountModalProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await onConfirm(password);
      // No onClose() here on success — the parent (AuthBar) is the one that
      // knows the account is gone (it owns the sign-in state) and closes
      // this modal itself. On failure, this modal deliberately stays open
      // with the error below so the account/session are visibly untouched.
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center" onClick={onClose}>
      <div
        className="w-full max-w-sm rounded-t-2xl bg-white p-6 pb-8 shadow-xl sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold text-slate-900">Delete account</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-11 w-11 items-center justify-center text-xl text-slate-400"
          >
            ✕
          </button>
        </div>

        <p className="mb-4 text-sm text-slate-600">
          This permanently deletes your account, saved results, and job history. This can't be undone.
        </p>

        <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-3">
          <input
            type="password"
            required
            placeholder="Current password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="min-h-11 rounded-lg border border-slate-200 px-3 py-2.5 text-sm"
            autoComplete="current-password"
          />

          {error && <p className="text-sm text-red-600">{error}</p>}

          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-1 rounded-full bg-red-600 px-4 py-3 font-semibold text-white active:scale-[0.98] disabled:opacity-50"
          >
            {isSubmitting ? "Deleting..." : "Permanently delete my account"}
          </button>
        </form>
      </div>
    </div>
  );
}
