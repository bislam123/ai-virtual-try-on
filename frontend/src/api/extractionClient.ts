import { apiFetch } from "./http";

export { ApiError } from "./http";

export interface ExtractionOutcome {
  file: File;
  applied: boolean;
  confidence: number;
}

async function outcomeFromResponse(response: Response, filename: string): Promise<ExtractionOutcome> {
  const blob = await response.blob();
  return {
    file: new File([blob], filename, { type: "image/png" }),
    applied: response.headers.get("X-Extraction-Applied") === "true",
    confidence: parseFloat(response.headers.get("X-Extraction-Confidence") ?? "0"),
  };
}

/** Milestone 7: POST /api/extract-product-image. Synchronous (classical CV,
 * not the AI model) — no job/poll pattern needed, unlike try-on. */
export async function extractProductImage(image: File): Promise<ExtractionOutcome> {
  const form = new FormData();
  form.append("image", image);
  const response = await apiFetch("/api/extract-product-image", { method: "POST", body: form });
  return outcomeFromResponse(response, image.name);
}

/** Milestone 8, brief section 7 Method A: POST /api/extract-product-url.
 * Also synchronous — see the endpoint's own docstring for why. */
export async function extractProductFromUrl(url: string): Promise<ExtractionOutcome> {
  const response = await apiFetch("/api/extract-product-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  return outcomeFromResponse(response, "product-from-url.png");
}
