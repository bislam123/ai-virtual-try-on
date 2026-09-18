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
