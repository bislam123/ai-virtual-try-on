/** Fetches the result as a blob rather than using the raw (cross-origin, in
 * dev) URL directly in an <a download>, which browsers can silently ignore
 * for cross-origin links — a same-origin blob: URL always downloads reliably. */
async function fetchResultBlob(resultUrl: string): Promise<Blob> {
  const response = await fetch(resultUrl);
  if (!response.ok) throw new Error("Couldn't fetch the result image.");
  return response.blob();
}

export async function downloadResultImage(resultUrl: string, filename = "ai-tryon-result.png"): Promise<void> {
  const blob = await fetchResultBlob(resultUrl);
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

export async function shareResultImage(resultUrl: string): Promise<"shared" | "downloaded" | "cancelled"> {
  const blob = await fetchResultBlob(resultUrl);
  const file = new File([blob], "ai-tryon-result.png", { type: blob.type || "image/png" });

  if (navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: "My AI Try-On result" });
      return "shared";
    } catch (err) {
      // AbortError = user cancelled the native share sheet — not a failure.
      if (err instanceof DOMException && err.name === "AbortError") return "cancelled";
      throw err;
    }
  }

  // Web Share API (with files) isn't available — e.g. most desktop browsers.
  // Falling back to a plain download still gets the user their image.
  await downloadResultImage(resultUrl);
  return "downloaded";
}
