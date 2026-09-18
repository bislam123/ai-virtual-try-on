import { useState } from "react";
import AuthModal from "./AuthModal";
import DeleteAccountModal from "./DeleteAccountModal";
import type { UserResponse } from "../types/auth";

interface AuthBarProps {
  user: UserResponse | null;
  onLogin: (email: string, password: string) => Promise<void>;
  onSignup: (email: string, password: string) => Promise<void>;
  onForgotPassword: (email: string) => Promise<void>;
  onLogout: () => void;
  onDeleteAccount: (password: string) => Promise<void>;
  // Optional: HomeScreen/App.tsx only pass this through when there's
  // somewhere for it to go. Backend authorization is what actually gates
  // the admin area (get_current_admin_user, re-checked on every admin
  // request) -- this button is convenience navigation, not a security
  // boundary, and is itself only ever shown when user.is_admin is true.
  onOpenAdmin?: () => void;
}

export default function AuthBar({
  user,
  onLogin,
  onSignup,
  onForgotPassword,
  onLogout,
  onDeleteAccount,
  onOpenAdmin,
}: AuthBarProps) {
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [justDeletedAccount, setJustDeletedAccount] = useState(false);

  const handleConfirmDelete = async (password: string) => {
    await onDeleteAccount(password); // throws on failure -- DeleteAccountModal shows the error and stays open
    setShowDeleteModal(false);
    setJustDeletedAccount(true);
  };

  return (
    <div className="flex min-h-11 items-center justify-end gap-3 text-sm">
      {user ? (
        <div className="flex items-center gap-3 text-slate-500">
          <span className="truncate max-w-[160px]">{user.email}</span>
          {user.is_admin && onOpenAdmin && (
            <button
              type="button"
              onClick={onOpenAdmin}
              className="flex min-h-11 items-center font-semibold text-indigo-600"
            >
              Admin
            </button>
          )}
          <button
            type="button"
            onClick={onLogout}
            className="flex min-h-11 items-center font-semibold text-indigo-600"
          >
            Sign out
          </button>
          <button
            type="button"
            onClick={() => setShowDeleteModal(true)}
            className="flex min-h-11 items-center text-xs text-red-500"
          >
            Delete account
          </button>
        </div>
      ) : justDeletedAccount ? (
        <span role="status" className="text-slate-500">
          Your account has been deleted.
        </span>
      ) : (
        <button
          type="button"
          onClick={() => setShowAuthModal(true)}
          className="flex min-h-11 items-center font-semibold text-indigo-600"
        >
          Sign in
        </button>
      )}

      {showAuthModal && (
        <AuthModal
          onClose={() => setShowAuthModal(false)}
          onLogin={onLogin}
          onSignup={onSignup}
          onForgotPassword={onForgotPassword}
        />
      )}
      {showDeleteModal && (
        <DeleteAccountModal onClose={() => setShowDeleteModal(false)} onConfirm={handleConfirmDelete} />
      )}
    </div>
  );
}
