import { apiFetch } from "./http";
import type { UsageStatus } from "../types/usage";

export { ApiError } from "./http";

/** Milestone 11: GET /api/usage/me. Works for anonymous callers too
 * (tracked by IP server-side, same identity resolution POST /api/try-on
 * itself uses) — lets the UI show "X of Y left today" honestly before the
 * user hits the limit, rather than only finding out from a 429 on submit. */
export async function getUsage(token?: string | null): Promise<UsageStatus> {
  const response = await apiFetch("/api/usage/me", undefined, token);
  return (await response.json()) as UsageStatus;
}
