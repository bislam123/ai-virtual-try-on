import { useState } from "react";
import { ApiError, extractProductFromUrl, type ExtractionOutcome } from "../api/extractionClient";

interface ProductUrlInputProps {
  onExtracted: (outcome: ExtractionOutcome) => void;
}

/** Brief section 17's wireframe: "[ Paste Product URL ]" — Method A from
 * section 7. Collapsed by default so it doesn't compete with "Upload
 * Clothing" for attention (avoid clutter). */
export default function ProductUrlInput({ onExtracted }: ProductUrlInputProps) {
  const [expanded, setExpanded] = useState(false);
  const [url, setUrl] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleFetch = async () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setIsLoading(true);
    setErrorMessage(null);
    try {
      const outcome = await extractProductFromUrl(trimmed);
      onExtracted(outcome);
      setExpanded(false);
      setUrl("");
    } catch (err) {
      // The backend's message already covers the brief's required fallback
      // guidance (section 25) — e.g. "...Please upload a photo instead."
      setErrorMessage(err instanceof ApiError ? err.message : "Couldn't process that link. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };

  if (!expanded) {
    return (
      <button type="button" onClick={() => setExpanded(true)} className="text-xs font-medium text-indigo-600">
        Or paste a product URL
      </button>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <input
          type="url"
          inputMode="url"
          placeholder="https://example.com/product/..."
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm"
        />
        <button
          type="button"
          onClick={() => void handleFetch()}
          disabled={isLoading || !url.trim()}
          className="shrink-0 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {isLoading ? "Fetching..." : "Fetch"}
        </button>
      </div>
      {errorMessage && <p className="text-xs text-red-600">{errorMessage}</p>}
    </div>
  );
}
