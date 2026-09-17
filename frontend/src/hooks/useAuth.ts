import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  deleteAccount as apiDeleteAccount,
  forgotPassword as apiForgotPassword,
  getMe,
  login as apiLogin,
  signup as apiSignup,
} from "../api/authClient";
import { setSessionExpiredListener } from "../api/http";
import type { UserResponse } from "../types/auth";

const TOKEN_STORAGE_KEY = "aitryon_auth_token";

// Wrapped in try/catch throughout: localStorage can throw (private browsing,
// blocked site data) and this is only a convenience (staying signed in
// across reloads) — the app must keep working without it.
function readStoredToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    else window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // ignore — see readStoredToken
  }
}

export function useAuth() {
  const [token, setToken] = useState<string | null>(() => readStoredToken());
  const [user, setUser] = useState<UserResponse | null>(null);
  const [isLoadingUser, setIsLoadingUser] = useState(false);

  // An effect, not derived state, because it fetches from the network (an
  // external system) whenever the token changes — same rationale as the
  // object-URL lifecycle effects in PhotoPicker.tsx/ResultScreen.tsx.
  useEffect(() => {
    if (!token) {
      setUser(null);
      return;
    }
    let cancelled = false;
    setIsLoadingUser(true);
    getMe(token)
      .then((u) => {
        if (!cancelled) setUser(u);
      })
      .catch(() => {
        // Token expired/invalid — sign out quietly rather than looping errors.
        if (!cancelled) {
          setToken(null);
          writeStoredToken(null);
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoadingUser(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Registered once, for the lifetime of this hook instance (an app only
  // ever mounts one) -- see api/http.ts's setSessionExpiredListener for why
  // this lives at the fetch layer rather than being threaded through every
  // individual API call site. Fires for ANY apiFetch call anywhere in the
  // app that carried the current token and got a 401 back, not just the
  // getMe() check above -- e.g. a stale tab still open after the token was
  // revoked elsewhere (password reset) tries to save a result and gets
  // signed out right then, rather than silently failing while still
  // looking signed in. Deliberately does the same three-line clear inline
  // rather than calling logout() -- matches the getMe .catch() above, which
  // does the same for the same reason (no reason to funnel this through
  // logout()'s own name/semantics when this isn't a user-initiated logout).
  useEffect(() => {
    setSessionExpiredListener(() => {
      setToken(null);
      writeStoredToken(null);
      setUser(null);
    });
    return () => setSessionExpiredListener(null);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const { access_token } = await apiLogin(email, password);
    writeStoredToken(access_token);
    setToken(access_token);
  }, []);

  const signup = useCallback(async (email: string, password: string) => {
    const { access_token } = await apiSignup(email, password);
    writeStoredToken(access_token);
    setToken(access_token);
  }, []);

  // Doesn't touch token/user state at all — forgot-password never signs
  // anyone in or out, it just triggers an email. Left as a thin passthrough
  // (rather than folded into the component) so callers depend on the same
  // useAuth() surface as login/signup/logout/deleteAccount.
  const forgotPassword = useCallback(async (email: string) => {
    await apiForgotPassword(email);
  }, []);

  const logout = useCallback(() => {
    writeStoredToken(null);
    setToken(null);
    setUser(null);
  }, []);

  // On success, clears session state via the exact same mechanism logout()
  // uses — deletion on the backend is already irreversible by that point,
  // so there's nothing left to keep signed into. On failure (e.g. wrong
  // password), nothing is cleared and the error propagates to the caller.
  const deleteAccount = useCallback(
    async (password: string) => {
      if (!token) throw new ApiError("Please sign in to use this feature.", 401);
      await apiDeleteAccount(password, token);
      logout();
    },
    [token, logout],
  );

  return { token, user, isLoadingUser, login, signup, forgotPassword, logout, deleteAccount };
}

export { ApiError };
