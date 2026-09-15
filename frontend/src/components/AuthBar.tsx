import { useState } from "react";
import AuthModal from "./AuthModal";
import type { UserResponse } from "../types/auth";

interface AuthBarProps {
  user: UserResponse | null;
  onLogin: (email: string, password: string) => Promise<void>;
  onSignup: (email: string, password: string) => Promise<void>;
  onLogout: () => void;
}

export default function AuthBar({ user, onLogin, onSignup, onLogout }: AuthBarProps) {
  const [showModal, setShowModal] = useState(false);

  return (
    <div className="flex justify-end text-sm">
      {user ? (
        <div className="flex items-center gap-2 text-slate-500">
          <span className="truncate max-w-[160px]">{user.email}</span>
          <button type="button" onClick={onLogout} className="font-semibold text-indigo-600">
            Sign out
          </button>
        </div>
      ) : (
        <button type="button" onClick={() => setShowModal(true)} className="font-semibold text-indigo-600">
          Sign in
        </button>
      )}

      {showModal && <AuthModal onClose={() => setShowModal(false)} onLogin={onLogin} onSignup={onSignup} />}
    </div>
  );
}
