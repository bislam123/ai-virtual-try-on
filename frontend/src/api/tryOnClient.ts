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

export async function getJobStatus(jobId: string, token?: string | null): Promise<TryOnJobStatusResponse> {
  // Passing the token when present matters, not just for anonymous jobs:
  // a job created while signed in is only visible to its owner (backend
  // enforces this), so an authenticated poll for the caller's own job
  // must carry the same token that created it.
  const response = await apiFetch(`/api/try-on/${jobId}`, undefined, token);
  return (await response.json()) as TryOnJobStatusResponse;
}

export async function saveTryOnResult(jobId: string, token: string): Promise<TryOnJobStatusResponse> {
  const response = await apiFetch(`/api/try-on/${jobId}/save`, { method: "POST" }, token);
  return (await response.json()) as TryOnJobStatusResponse;
}

export function getResultImageUrl(jobId: string): string {
  return `${API_BASE_URL}/api/try-on/${jobId}/result`;
}

/** The result endpoint enforces the same ownership rule as job status (see
 * getJobStatus above) -- fetched here (not a bare <img src>, which can
 * never carry an Authorization header at all) specifically so an
 * authenticated owner's own result still loads. Callers turn the returned
 * Blob into an object URL (see ResultScreen.tsx), same pattern already
 * used for the local photo preview. */
export async function fetchResultImageBlob(jobId: string, token?: string | null): Promise<Blob> {
  // Relative path, not getResultImageUrl(jobId) -- apiFetch prepends
  // API_BASE_URL itself, and that helper already returns a full URL (used
  // directly by things outside apiFetch, e.g. an <a> download href).
  const response = await apiFetch(`/api/try-on/${jobId}/result`, undefined, token);
  return response.blob();
}
