import { useState, type FormEvent } from "react";
import { ApiError } from "../api/http";

interface AuthModalProps {
  onClose: () => void;
  onLogin: (email: string, password: string) => Promise<void>;
  onSignup: (email: string, password: string) => Promise<void>;
  onForgotPassword: (email: string) => Promise<void>;
}

type Mode = "login" | "signup" | "forgot";

export default function AuthModal({ onClose, onLogin, onSignup, onForgotPassword }: AuthModalProps) {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // Separate from `error`/form state: once true, the modal shows a generic
  // confirmation instead of the form -- see the same generic-response
  // contract as the backend's own POST /api/auth/forgot-password (never
  // reveals whether the email is registered, see authClient.forgotPassword).
  const [forgotPasswordSent, setForgotPasswordSent] = useState(false);

  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
    setForgotPasswordSent(false);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      if (mode === "login") {
        await onLogin(email, password);
        onClose();
      } else if (mode === "signup") {
        await onSignup(email, password);
        onClose();
      } else {
        await onForgotPassword(email);
        setForgotPasswordSent(true);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const title = mode === "login" ? "Sign in" : mode === "signup" ? "Create account" : "Reset your password";

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center" onClick={onClose}>
      <div
        className="w-full max-w-sm rounded-t-2xl bg-white p-6 pb-8 shadow-xl sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold text-slate-900">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-11 w-11 items-center justify-center text-xl text-slate-400"
          >
            ✕
          </button>
        </div>

        {mode === "forgot" && forgotPasswordSent ? (
          <div className="flex flex-col gap-4">
            <p className="text-sm text-slate-600">
              If an account exists for that email, we've sent a link to reset your password. Check your inbox.
            </p>
            <button
              type="button"
              onClick={() => switchMode("login")}
              className="mt-1 min-h-11 rounded-full bg-indigo-600 px-4 py-3 font-semibold text-white active:scale-[0.98]"
            >
              Back to sign in
            </button>
          </div>
        ) : (
          <>
            <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-3">
              <input
                type="email"
                required
                placeholder="Email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="min-h-11 rounded-lg border border-slate-200 px-3 py-2.5 text-sm"
                autoComplete="email"
              />
              {mode !== "forgot" && (
                <input
                  type="password"
                  required
                  minLength={mode === "signup" ? 8 : undefined}
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="min-h-11 rounded-lg border border-slate-200 px-3 py-2.5 text-sm"
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                />
              )}

              {mode === "login" && (
                <button
                  type="button"
                  onClick={() => switchMode("forgot")}
                  className="self-end text-xs font-medium text-indigo-600"
                >
                  Forgot password?
                </button>
              )}

              {error && <p className="text-sm text-red-600">{error}</p>}

              <button
                type="submit"
                disabled={isSubmitting}
                className="mt-1 rounded-full bg-indigo-600 px-4 py-3 font-semibold text-white active:scale-[0.98] disabled:opacity-50"
              >
                {mode === "login" ? "Sign in" : mode === "signup" ? "Create account" : "Send reset link"}
              </button>
            </form>

            {mode === "forgot" ? (
              <button
                type="button"
                onClick={() => switchMode("login")}
                className="mt-4 flex min-h-11 w-full items-center justify-center text-center text-sm text-indigo-600"
              >
                Back to sign in
              </button>
            ) : (
              <button
                type="button"
                onClick={() => switchMode(mode === "login" ? "signup" : "login")}
                className="mt-4 flex min-h-11 w-full items-center justify-center text-center text-sm text-indigo-600"
              >
                {mode === "login" ? "New here? Create an account" : "Already have an account? Sign in"}
              </button>
            )}

            <p className="mt-3 text-center text-xs text-slate-400">
              An account is only needed to save results to a permanent library — you can try the app without one.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
