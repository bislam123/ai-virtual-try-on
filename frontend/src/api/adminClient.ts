import { apiFetch } from "./http";
import type {
  AdminAuditLogListResponse,
  AdminDashboard,
  AdminJobListResponse,
  AdminPlan,
  AdminPlanUpdateRequest,
  AdminUserDetail,
  AdminUserListResponse,
  AdminUserSummary,
} from "../types/admin";

export { ApiError } from "./http";

/** Every call here requires a token and hits an admin-only route -- the
 * backend independently re-verifies is_admin on every one of these
 * regardless of what the frontend believes (see auth/dependencies.py's
 * get_current_admin_user); a non-admin or missing token surfaces as a
 * normal ApiError (403/401) the caller handles like any other. */

export async function listAdminUsers(
  token: string,
  params?: { search?: string; limit?: number; offset?: number },
): Promise<AdminUserListResponse> {
  const query = new URLSearchParams();
  if (params?.search) query.set("search", params.search);
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  const qs = query.toString();
  const response = await apiFetch(`/api/admin/users${qs ? `?${qs}` : ""}`, undefined, token);
  return (await response.json()) as AdminUserListResponse;
}

export async function getAdminUser(userId: number, token: string): Promise<AdminUserDetail> {
  const response = await apiFetch(`/api/admin/users/${userId}`, undefined, token);
  return (await response.json()) as AdminUserDetail;
}

export async function disableAdminUser(userId: number, token: string): Promise<AdminUserSummary> {
  const response = await apiFetch(`/api/admin/users/${userId}/disable`, { method: "POST" }, token);
  return (await response.json()) as AdminUserSummary;
}

export async function enableAdminUser(userId: number, token: string): Promise<AdminUserSummary> {
  const response = await apiFetch(`/api/admin/users/${userId}/enable`, { method: "POST" }, token);
  return (await response.json()) as AdminUserSummary;
}

export async function listAdminPlans(token: string): Promise<AdminPlan[]> {
  const response = await apiFetch("/api/admin/plans", undefined, token);
  return (await response.json()) as AdminPlan[];
}

export async function updateAdminPlan(
  name: string,
  body: AdminPlanUpdateRequest,
  token: string,
): Promise<AdminPlan> {
  const response = await apiFetch(
    `/api/admin/plans/${encodeURIComponent(name)}`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    token,
  );
  return (await response.json()) as AdminPlan;
}

export async function listAdminJobs(
  token: string,
  params?: { status?: string; userId?: number; limit?: number; offset?: number },
): Promise<AdminJobListResponse> {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.userId !== undefined) query.set("user_id", String(params.userId));
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  const qs = query.toString();
  const response = await apiFetch(`/api/admin/jobs${qs ? `?${qs}` : ""}`, undefined, token);
  return (await response.json()) as AdminJobListResponse;
}

export async function listAdminAuditLog(
  token: string,
  params?: { targetType?: string; targetId?: string; limit?: number; offset?: number },
): Promise<AdminAuditLogListResponse> {
  const query = new URLSearchParams();
  if (params?.targetType) query.set("target_type", params.targetType);
  if (params?.targetId) query.set("target_id", params.targetId);
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  const qs = query.toString();
  const response = await apiFetch(`/api/admin/audit-log${qs ? `?${qs}` : ""}`, undefined, token);
  return (await response.json()) as AdminAuditLogListResponse;
}

export async function getAdminDashboard(token: string): Promise<AdminDashboard> {
  const response = await apiFetch("/api/admin/dashboard", undefined, token);
  return (await response.json()) as AdminDashboard;
}
