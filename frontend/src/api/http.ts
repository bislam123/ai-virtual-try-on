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
    throw new ApiError(await readErrorDetail(response), response.status);
  }
  return response;
}
