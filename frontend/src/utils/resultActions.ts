import { fetchResultImageBlob } from "../api/tryOnClient";

export async function downloadResultImage(
  jobId: string,
  token: string | null,
  filename = "ai-tryon-result.png",
): Promise<void> {
  const blob = await fetchResultImageBlob(jobId, token);
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

export async function shareResultImage(jobId: string, token: string | null): Promise<"shared" | "downloaded" | "cancelled"> {
  const blob = await fetchResultImageBlob(jobId, token);
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
  await downloadResultImage(jobId, token);
  return "downloaded";
}
