import { useState, type FormEvent } from "react";
import { ApiError } from "../api/http";
import { resetPassword } from "../api/authClient";

interface ResetPasswordScreenProps {
  /** The raw reset token read from the URL by App.tsx — never logged or
   * echoed anywhere else by this screen. */
  token: string;
  onDone: () => void;
}

type Status = "form" | "success";

export default function ResetPasswordScreen({ token, onDone }: ResetPasswordScreenProps) {
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [status, setStatus] = useState<Status>("form");

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password !== confirmPassword) {
      setError("Passwords don't match.");
      return;
    }
    setIsSubmitting(true);
    try {
      await resetPassword(token, password);
      setStatus("success");
    } catch (err) {
      // The backend intentionally returns one generic message for every
      // invalid/expired/already-used token (see backend/app/api/auth.py's
      // RESET_TOKEN_INVALID_ERROR) -- shown here exactly as received,
      // never re-interpreted into something more specific that could hint
      // at which of those actually happened.
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 px-4 py-8">
      <header className="text-center">
        <h1 className="text-2xl font-bold text-slate-900">Reset your password</h1>
      </header>

      <section className="flex flex-col gap-4 rounded-2xl bg-white p-6 shadow-sm">
        {status === "success" ? (
          <div role="status" className="flex flex-col gap-4">
            <p className="text-sm text-slate-600">
              Your password has been reset. You can now sign in with your new password.
            </p>
            <button
              type="button"
              onClick={onDone}
              className="mt-1 min-h-11 rounded-full bg-indigo-600 px-4 py-3 font-semibold text-white active:scale-[0.98]"
            >
              Continue
            </button>
          </div>
        ) : (
          <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-3">
            <label htmlFor="reset-password-new" className="sr-only">
              New password
            </label>
            <input
              id="reset-password-new"
              type="password"
              required
              minLength={8}
              placeholder="New password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="min-h-11 rounded-lg border border-slate-200 px-3 py-2.5 text-sm"
              autoComplete="new-password"
            />
            <label htmlFor="reset-password-confirm" className="sr-only">
              Confirm new password
            </label>
            <input
              id="reset-password-confirm"
              type="password"
              required
              minLength={8}
              placeholder="Confirm new password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="min-h-11 rounded-lg border border-slate-200 px-3 py-2.5 text-sm"
              autoComplete="new-password"
            />

            {error && (
              <p role="alert" className="text-sm text-red-600">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={isSubmitting}
              className="mt-1 rounded-full bg-indigo-600 px-4 py-3 font-semibold text-white active:scale-[0.98] disabled:opacity-50"
            >
              {isSubmitting ? "Resetting..." : "Reset password"}
            </button>
            <button
              type="button"
              onClick={onDone}
              className="flex min-h-11 w-full items-center justify-center text-center text-sm text-indigo-600"
            >
              Cancel
            </button>
          </form>
        )}
      </section>
    </div>
  );
}
