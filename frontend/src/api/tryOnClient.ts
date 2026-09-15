import type { GarmentCategory, TryOnJobCreated, TryOnJobStatusResponse } from "../types/tryOn";

// Configurable per environment (see .env.example) — never hardcode a deployed
// backend URL in committed source. Defaults to the local FastAPI dev server.
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const GENERIC_NETWORK_ERROR = "We couldn't reach the server. Please check your connection and try again.";

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

export async function submitTryOnJob(
  personImage: File,
  garmentImage: File,
  category: GarmentCategory,
): Promise<TryOnJobCreated> {
  const form = new FormData();
  form.append("person_image", personImage);
  form.append("garment_image", garmentImage);
  form.append("category", category);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/try-on`, { method: "POST", body: form });
  } catch {
    throw new ApiError(GENERIC_NETWORK_ERROR, 0);
  }

  if (!response.ok) {
    throw new ApiError(await readErrorDetail(response), response.status);
  }
  return (await response.json()) as TryOnJobCreated;
}

export async function getJobStatus(jobId: string): Promise<TryOnJobStatusResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/try-on/${jobId}`);
  } catch {
    throw new ApiError(GENERIC_NETWORK_ERROR, 0);
  }
  if (!response.ok) {
    throw new ApiError(await readErrorDetail(response), response.status);
  }
  return (await response.json()) as TryOnJobStatusResponse;
}

export function getResultImageUrl(jobId: string): string {
  return `${API_BASE_URL}/api/try-on/${jobId}/result`;
}
