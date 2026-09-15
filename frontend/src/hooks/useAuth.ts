import { useCallback, useEffect, useState } from "react";
import { ApiError, getMe, login as apiLogin, signup as apiSignup } from "../api/authClient";
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

  const logout = useCallback(() => {
    writeStoredToken(null);
    setToken(null);
    setUser(null);
  }, []);

  return { token, user, isLoadingUser, login, signup, logout };
}

export { ApiError };
