import { apiFetch } from "./http";

export { ApiError } from "./http";

export interface ExtractionOutcome {
  file: File;
  applied: boolean;
  confidence: number;
}

/** Milestone 7: POST /api/extract-product-image. Synchronous (classical CV,
 * not the AI model) — no job/poll pattern needed, unlike try-on. */
export async function extractProductImage(image: File): Promise<ExtractionOutcome> {
  const form = new FormData();
  form.append("image", image);

  const response = await apiFetch("/api/extract-product-image", { method: "POST", body: form });
  const blob = await response.blob();
  return {
    file: new File([blob], image.name, { type: "image/png" }),
    applied: response.headers.get("X-Extraction-Applied") === "true",
    confidence: parseFloat(response.headers.get("X-Extraction-Confidence") ?? "0"),
  };
}
