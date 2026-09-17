export type GarmentCategory = "tops" | "bottoms" | "one-pieces";

export type JobStatus = "pending" | "processing" | "completed" | "failed" | "cancelled";

export interface TryOnJobCreated {
  job_id: string;
  status: JobStatus;
}

export interface TryOnJobStatusResponse {
  job_id: string;
  status: JobStatus;
  error: string | null;
  created_at: string;
  updated_at: string;
  result_url: string | null;
  saved: boolean;
}
