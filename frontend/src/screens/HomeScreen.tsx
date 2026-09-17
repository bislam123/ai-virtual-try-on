import { useState } from "react";
import { ApiError, extractProductImage, type ExtractionOutcome } from "../api/extractionClient";
import AuthBar from "../components/AuthBar";
import PhotoPicker from "../components/PhotoPicker";
import ProductUrlInput from "../components/ProductUrlInput";
import UsageIndicator from "../components/UsageIndicator";
import type { UserResponse } from "../types/auth";
import type { GarmentCategory } from "../types/tryOn";

type ExtractionState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; applied: boolean }
  | { status: "error"; message: string };

const CATEGORY_OPTIONS: { value: GarmentCategory; label: string }[] = [
  { value: "tops", label: "Top" },
  { value: "bottoms", label: "Bottoms" },
  { value: "one-pieces", label: "Dress / Full outfit" },
];

interface HomeScreenProps {
  personImage: File | null;
  setPersonImage: (file: File | null) => void;
  garmentImage: File | null;
  setGarmentImage: (file: File | null) => void;
  category: GarmentCategory;
  setCategory: (category: GarmentCategory) => void;
  errorMessage: string | null;
  onDismissError: () => void;
  onSubmit: () => void;
  user: UserResponse | null;
  authToken: string | null;
  onLogin: (email: string, password: string) => Promise<void>;
  onSignup: (email: string, password: string) => Promise<void>;
  onLogout: () => void;
  onDeleteAccount: (password: string) => Promise<void>;
}

export default function HomeScreen({
  personImage,
  setPersonImage,
  garmentImage,
  setGarmentImage,
  category,
  setCategory,
  errorMessage,
  onDismissError,
  onSubmit,
  user,
  authToken,
  onLogin,
  onSignup,
  onLogout,
  onDeleteAccount,
}: HomeScreenProps) {
  const canSubmit = personImage !== null && garmentImage !== null;
  const [extractionState, setExtractionState] = useState<ExtractionState>({ status: "idle" });

  // Milestone 9's browser extension hands off a product by opening this
  // app with a query param: ?productUrl= originally, and (added in an
  // extension update) ?productImageUrl= — the more reliable image-handoff
  // path, see ProductUrlInput's initialImageUrl. Read them exactly once
  // (a lazy useState initializer runs only on the very first render — see
  // ProcessingScreen.tsx for the same pattern) and strip all of them from
  // the URL immediately, so returning to this screen later (e.g. "Try
  // Another") never re-triggers the same fetch.
  const [initialGarmentSource] = useState<{ url?: string; imageUrl?: string }>(() => {
    const params = new URLSearchParams(window.location.search);
    const productUrl = params.get("productUrl") ?? undefined;
    const productImageUrl = params.get("productImageUrl") ?? undefined;
    if (productUrl || productImageUrl) {
      params.delete("productUrl");
      params.delete("productImageUrl");
      params.delete("sourceUrl"); // extension-provided context only; not yet surfaced in the UI
      const newSearch = params.toString();
      window.history.replaceState({}, "", window.location.pathname + (newSearch ? `?${newSearch}` : ""));
    }
    return { url: productUrl, imageUrl: productImageUrl };
  });

  const handleGarmentChange = (file: File | null) => {
    setExtractionState({ status: "idle" });
    setGarmentImage(file);
  };

  const handleAutoDetect = async () => {
    if (!garmentImage) return;
    setExtractionState({ status: "loading" });
    try {
      const outcome = await extractProductImage(garmentImage);
      setGarmentImage(outcome.file); // note: bypasses handleGarmentChange on purpose, so the status below survives
      setExtractionState({ status: "done", applied: outcome.applied });
    } catch (err) {
      setExtractionState({
        status: "error",
        message: err instanceof ApiError ? err.message : "Couldn't process that photo. Please try again.",
      });
    }
  };

  const handleUrlExtracted = (outcome: ExtractionOutcome) => {
    // The backend already ran the same saliency crop a plain upload can
    // trigger manually (Milestone 7's extractor), so this reuses the same
    // "done" status display rather than introducing a separate one.
    setGarmentImage(outcome.file);
    setExtractionState({ status: "done", applied: outcome.applied });
  };

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col gap-6 px-4 pb-10 pt-8">
      <AuthBar
        user={user}
        onLogin={onLogin}
        onSignup={onSignup}
        onLogout={onLogout}
        onDeleteAccount={onDeleteAccount}
      />

      <header className="text-center">
        <h1 className="text-3xl font-bold text-slate-900">✨ Try It On</h1>
        <p className="mt-1 text-sm text-slate-500">See how clothes look on you</p>
      </header>

      {errorMessage && (
        <div className="flex items-start justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <p>{errorMessage}</p>
          <button
            type="button"
            onClick={onDismissError}
            aria-label="Dismiss"
            className="flex h-11 w-11 shrink-0 items-center justify-center text-base font-bold"
          >
            ✕
          </button>
        </div>
      )}

      <section className="flex flex-col gap-4 rounded-2xl bg-white p-4 shadow-sm">
        <PhotoPicker
          title="Your photo"
          file={personImage}
          onChange={setPersonImage}
          previewAlt="Your selected photo"
          buttons={[
            { label: "Take Your Photo", capture: "user" },
            { label: "Choose Your Photo" },
          ]}
        />
      </section>

      <div className="text-center text-xl text-slate-300">+</div>

      <section className="flex flex-col gap-4 rounded-2xl bg-white p-4 shadow-sm">
        <PhotoPicker
          title="Clothing photo"
          file={garmentImage}
          onChange={handleGarmentChange}
          previewAlt="Selected clothing photo"
          buttons={[{ label: "Upload Clothing" }]}
        />

        {!garmentImage && (
          <ProductUrlInput
            onExtracted={handleUrlExtracted}
            initialUrl={initialGarmentSource.url}
            initialImageUrl={initialGarmentSource.imageUrl}
          />
        )}

        {garmentImage && (
          <div className="flex flex-col gap-1">
            <button
              type="button"
              onClick={() => void handleAutoDetect()}
              disabled={extractionState.status === "loading"}
              className="min-h-11 rounded-lg border border-dashed border-slate-300 px-3 py-2 text-xs font-medium text-slate-600 disabled:opacity-60"
            >
              {extractionState.status === "loading" ? "Detecting..." : "✂ Auto-detect clothing in photo"}
            </button>
            {extractionState.status === "done" && (
              <p className="text-xs text-slate-500">
                {extractionState.applied
                  ? "✓ Cropped to the clothing item."
                  : "This already looks like a clean product photo — no changes made."}
              </p>
            )}
            {extractionState.status === "error" && <p className="text-xs text-red-600">{extractionState.message}</p>}
          </div>
        )}

        {garmentImage && (
          <div>
            <p className="mb-2 text-sm font-medium text-slate-700">What kind of clothing is this?</p>
            <div className="flex gap-2">
              {CATEGORY_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setCategory(opt.value)}
                  className={`min-h-11 flex-1 rounded-lg border px-2 py-2 text-xs font-semibold transition ${
                    category === opt.value
                      ? "border-indigo-600 bg-indigo-600 text-white"
                      : "border-slate-200 bg-white text-slate-600"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        )}
      </section>

      <button
        type="button"
        disabled={!canSubmit}
        onClick={onSubmit}
        className="mt-2 rounded-full bg-indigo-600 px-6 py-4 text-lg font-bold text-white shadow-md transition active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        Try It On
      </button>

      <UsageIndicator authToken={authToken} />

      <p className="text-center text-xs text-slate-400">
        Your photos are used only to generate this result and aren't kept unless you choose to save it.
      </p>
    </div>
  );
}
