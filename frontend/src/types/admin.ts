import type { UsageStatus } from "./usage";

export interface AdminUserSummary {
  id: number;
  email: string;
  plan: string;
  created_at: string;
  is_admin: boolean;
  is_active: boolean;
}

export interface AdminUserListResponse {
  users: AdminUserSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminUserDetail extends AdminUserSummary {
  usage: UsageStatus;
}

export interface AdminPlan {
  name: string;
  max_generations_per_day: number | null;
  max_generations_per_month: number | null;
  max_num_timesteps: number | null;
}

// All three keys are always required -- `null` explicitly means
// "unlimited" for that field, while an omitted key is a 422 (see the
// backend's PlanUpdateRequest docstring, models/schemas.py). This
// endpoint always edits every limit together; there's no partial-update
// shape to represent here.
export interface AdminPlanUpdateRequest {
  max_generations_per_day: number | null;
  max_generations_per_month: number | null;
  max_num_timesteps: number | null;
}

export type AdminJobStatus = "pending" | "processing" | "completed" | "failed" | "cancelled";

export interface AdminJobSummary {
  job_id: string;
  user_id: number | null;
  status: AdminJobStatus;
  category: string;
  garment_photo_type: string;
  error: string | null;
  created_at: string;
  updated_at: string;
  processing_duration_seconds: number | null;
}

export interface AdminJobListResponse {
  jobs: AdminJobSummary[];
  total: number;
  limit: number;
  offset: number;
}

// Mirrors backend/app/models/schemas.py's AdminAuditLogEntry exactly --
// `details` is a small, backend-reviewed-safe JSON blob (see
// db/models.py's AdminAuditLog docstring: never a password/JWT/reset-
// token/other credential), and `admin_user_id` is the only actor
// identifier the API provides -- no email join exists (see
// api/admin.py's _audit_entry), so the UI never invents one.
export interface AdminAuditLogEntry {
  id: number;
  admin_user_id: number | null;
  action: string;
  target_type: string;
  target_id: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface AdminAuditLogListResponse {
  entries: AdminAuditLogEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminDashboard {
  total_users: number;
  active_users: number;
  jobs_by_status: Record<string, number>;
  recent_failed_jobs: AdminJobSummary[];
  active_job_count: number;
  max_active_job_capacity: number;
  avg_processing_duration_seconds: number | null;
}
