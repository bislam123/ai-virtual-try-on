// Configurable per environment (see .env.example) — never hardcode a deployed
// backend URL in committed source. Defaults to the local FastAPI dev server.
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const GENERIC_NETWORK_ERROR = "We couldn't reach the server. Please check your connection and try again.";

type SessionExpiredListener = () => void;

// useAuth() registers itself here once, on mount (see useAuth.ts), so any
// apiFetch call that carried a bearer token and got back a 401 can clear
// that token's now-stale session state globally -- not just surface an
// error at the one call site that happened to trigger it (e.g. the user
// resets their password in another tab, or the backend revokes the token
// for any other reason, then this tab tries to save a result). A single
// slot, not an array/event target: this app has exactly one signed-in
// session at a time (see useAuth.ts), so at most one listener is ever
// meaningful. A 401 on a request that *carried* a token specifically means
// the token itself is no longer valid -- the only backend source of a 401
// on an authenticated request is auth/dependencies.py's
// get_current_user_required; account-deletion's wrong-confirmation-password
// check deliberately uses 403, not 401, precisely so it's never confused
// with this (see backend/app/api/auth.py's delete_account).
let sessionExpiredListener: SessionExpiredListener | null = null;

export function setSessionExpiredListener(listener: SessionExpiredListener | null): void {
  sessionExpiredListener = listener;
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (body?.detail) return body.detail;
  } catch {
    // response wasn't JSON — fall through to a generic message rather than
    // ever showing raw response text to the user.
  }
  return "Something went wrong. Please try again.";
}

/** Thin fetch wrapper: network failures and non-2xx responses both become a
 * single ApiError with a message that's always safe to show the user
 * directly, never a raw response body or stack trace. */
export async function apiFetch(path: string, init?: RequestInit, token?: string | null): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(GENERIC_NETWORK_ERROR, 0);
  }
  if (!response.ok) {
    if (token && response.status === 401) {
      sessionExpiredListener?.();
    }
    throw new ApiError(await readErrorDetail(response), response.status);
  }
  return response;
}
