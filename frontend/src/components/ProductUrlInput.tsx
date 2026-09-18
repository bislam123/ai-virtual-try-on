import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  extractProductFromUrl,
  extractProductImageFromUrl,
  type ExtractionOutcome,
} from "../api/extractionClient";

interface ProductUrlInputProps {
  onExtracted: (outcome: ExtractionOutcome) => void;
  /** Pre-fills and immediately fetches — the handoff from the browser
   * extension (Milestone 9) lands here via ?productUrl= in HomeScreen. */
  initialUrl?: string;
  /** The extension's image-handoff path (added alongside Milestone 9's
   * extension work): a direct product image URL it already found on the
   * page (JSON-LD/og:image), rather than a page URL to re-scrape. Takes
   * priority over initialUrl when both are present, since it's the more
   * reliable path (works on CAPTCHA-gated sites where re-scraping the
   * page would fail). */
  initialImageUrl?: string;
}

/** Brief section 17's wireframe: "[ Paste Product URL ]" — Method A from
 * section 7. Collapsed by default so it doesn't compete with "Upload
 * Clothing" for attention (avoid clutter). */
export default function ProductUrlInput({ onExtracted, initialUrl, initialImageUrl }: ProductUrlInputProps) {
  const [expanded, setExpanded] = useState(Boolean(initialUrl || initialImageUrl));
  const [url, setUrl] = useState(initialUrl ?? "");
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const runExtraction = useCallback(
    async (extract: () => Promise<ExtractionOutcome>) => {
      setIsLoading(true);
      setErrorMessage(null);
      try {
        const outcome = await extract();
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
    },
    [onExtracted],
  );

  const fetchUrl = useCallback(
    async (target: string) => {
      const trimmed = target.trim();
      if (!trimmed) return;
      await runExtraction(() => extractProductFromUrl(trimmed));
    },
    [runExtraction],
  );

  // Auto-fetch once when arriving with a pre-filled URL or image URL (the
  // extension handoff). The ref guard — not an empty dependency array — is
  // what actually makes this "once": HomeScreen.tsx clears the query params
  // immediately after reading them, so these props never change in
  // practice, but this stays correct even if the callbacks' identities ever
  // did.
  const hasAutoFetchedRef = useRef(false);
  useEffect(() => {
    if (hasAutoFetchedRef.current) return;
    if (initialImageUrl) {
      hasAutoFetchedRef.current = true;
      void runExtraction(() => extractProductImageFromUrl(initialImageUrl));
    } else if (initialUrl) {
      hasAutoFetchedRef.current = true;
      void fetchUrl(initialUrl);
    }
  }, [initialUrl, initialImageUrl, fetchUrl, runExtraction]);

  if (!expanded) {
    return (
      <button
        type="button"
        aria-expanded={false}
        onClick={() => setExpanded(true)}
        className="flex min-h-11 items-center text-xs font-medium text-indigo-600"
      >
        Or paste a product URL
      </button>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor="product-url-input" className="sr-only">
        Product page URL
      </label>
      <div className="flex gap-2">
        <input
          id="product-url-input"
          type="url"
          inputMode="url"
          placeholder="https://example.com/product/..."
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="min-h-11 min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm"
        />
        <button
          type="button"
          onClick={() => void fetchUrl(url)}
          disabled={isLoading || !url.trim()}
          className="min-h-11 shrink-0 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {isLoading ? "Fetching..." : "Fetch"}
        </button>
      </div>
      {errorMessage && (
        <p role="alert" className="text-xs text-red-600">
          {errorMessage}
        </p>
      )}
    </div>
  );
}
