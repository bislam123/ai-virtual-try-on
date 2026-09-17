import { apiFetch } from "./http";
import type { AuthResponse, MessageResponse, UserResponse } from "../types/auth";

export { ApiError } from "./http";

export async function signup(email: string, password: string): Promise<AuthResponse> {
  const response = await apiFetch("/api/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return (await response.json()) as AuthResponse;
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  const response = await apiFetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return (await response.json()) as AuthResponse;
}

/** Backend always returns the same generic message whether or not `email`
 * belongs to a real account — never reveals account existence (see
 * backend/app/api/auth.py's forgot_password). */
export async function forgotPassword(email: string): Promise<MessageResponse> {
  const response = await apiFetch("/api/auth/forgot-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  return (await response.json()) as MessageResponse;
}

export async function resetPassword(token: string, newPassword: string): Promise<MessageResponse> {
  const response = await apiFetch("/api/auth/reset-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  return (await response.json()) as MessageResponse;
}

export async function getMe(token: string): Promise<UserResponse> {
  const response = await apiFetch("/api/auth/me", undefined, token);
  return (await response.json()) as UserResponse;
}

/** Backend returns 204 No Content on success — nothing to parse. Requires
 * the current password (backend-enforced), so a leaked/stolen token alone
 * can't trigger this irreversible action. */
export async function deleteAccount(password: string, token: string): Promise<void> {
  await apiFetch(
    "/api/auth/me",
    { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }) },
    token,
  );
}
