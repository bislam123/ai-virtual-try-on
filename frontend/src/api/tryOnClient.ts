import { API_BASE_URL, apiFetch } from "./http";
import type { GarmentCategory, TryOnJobCreated, TryOnJobStatusResponse } from "../types/tryOn";

export { ApiError } from "./http";

export async function submitTryOnJob(
  personImage: File,
  garmentImage: File,
  category: GarmentCategory,
  token?: string | null,
): Promise<TryOnJobCreated> {
  const form = new FormData();
  form.append("person_image", personImage);
  form.append("garment_image", garmentImage);
  form.append("category", category);

  const response = await apiFetch("/api/try-on", { method: "POST", body: form }, token);
  return (await response.json()) as TryOnJobCreated;
}

export async function getJobStatus(jobId: string): Promise<TryOnJobStatusResponse> {
  const response = await apiFetch(`/api/try-on/${jobId}`);
  return (await response.json()) as TryOnJobStatusResponse;
}

export async function saveTryOnResult(jobId: string, token: string): Promise<TryOnJobStatusResponse> {
  const response = await apiFetch(`/api/try-on/${jobId}/save`, { method: "POST" }, token);
  return (await response.json()) as TryOnJobStatusResponse;
}

export function getResultImageUrl(jobId: string): string {
  return `${API_BASE_URL}/api/try-on/${jobId}/result`;
}
