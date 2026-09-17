import { useEffect, useState } from "react";
import { ApiError, fetchResultImageBlob, saveTryOnResult } from "../api/tryOnClient";
import { downloadResultImage, shareResultImage } from "../utils/resultActions";

interface ResultScreenProps {
  personImage: File;
  jobId: string;
  saved: boolean;
  authToken: string | null;
  onSaved: () => void;
  onTryAnother: () => void;
  onChangeClothing: () => void;
}

export default function ResultScreen({
  personImage,
  jobId,
  saved,
  authToken,
  onSaved,
  onTryAnother,
  onChangeClothing,
}: ResultScreenProps) {
  const [personPreviewUrl, setPersonPreviewUrl] = useState<string | null>(null);
  const [resultPreviewUrl, setResultPreviewUrl] = useState<string | null>(null);
  const [resultLoadError, setResultLoadError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  // See the identical pattern (and why it's an effect, not derived state) in
  // components/PhotoPicker.tsx.
  useEffect(() => {
    const url = URL.createObjectURL(personImage);
    setPersonPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [personImage]);

  // The result image can no longer be a plain <img src="..."> -- the
  // backend now enforces per-owner access on this job (see
  // backend/app/services/tryon_service.py's get_job_for_viewer), and a
  // browser never attaches an Authorization header to a plain <img> tag.
  // Fetched here (carrying authToken when present) and turned into a
  // same-origin object URL instead, same lifecycle pattern as the person
  // preview above.
  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setResultPreviewUrl(null);
    setResultLoadError(null);

    fetchResultImageBlob(jobId, authToken)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setResultPreviewUrl(objectUrl);
      })
      .catch((err) => {
        if (cancelled) return;
        setResultLoadError(err instanceof ApiError ? err.message : "Couldn't load your result image.");
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [jobId, authToken]);

  const handleSave = async () => {
    setIsBusy(true);
    setActionMessage(null);
    try {
      await downloadResultImage(jobId, authToken);
      setActionMessage("Saved to your device.");
    } catch {
      setActionMessage("Couldn't save the image. Please try again.");
    } finally {
      setIsBusy(false);
    }
  };

  const handleShare = async () => {
    setIsBusy(true);
    setActionMessage(null);
    try {
      const outcome = await shareResultImage(jobId, authToken);
      if (outcome === "downloaded") setActionMessage("Sharing isn't available here — saved to your device instead.");
    } catch {
      setActionMessage("Couldn't share the image. Please try again.");
    } finally {
      setIsBusy(false);
    }
  };

  const handleSaveToAccount = async () => {
    if (!authToken) return;
    setIsBusy(true);
    setActionMessage(null);
    try {
      await saveTryOnResult(jobId, authToken);
      onSaved();
      setActionMessage("Saved to your account.");
    } catch (err) {
      setActionMessage(err instanceof ApiError ? err.message : "Couldn't save to your account. Please try again.");
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col gap-5 px-4 pb-10 pt-8">
      <header className="text-center">
        <h1 className="text-2xl font-bold text-slate-900">Your Try-On Result</h1>
      </header>

      <section className="rounded-2xl bg-white p-3 shadow-sm">
        {resultPreviewUrl ? (
          <img src={resultPreviewUrl} alt="Try-on result" className="w-full rounded-xl object-contain" />
        ) : resultLoadError ? (
          <p className="p-4 text-center text-sm text-red-600">{resultLoadError}</p>
        ) : (
          <div className="flex h-64 items-center justify-center text-sm text-slate-400">Loading your result…</div>
        )}
      </section>

      {personPreviewUrl && (
        <details className="rounded-xl bg-white p-3 text-sm shadow-sm">
          <summary className="cursor-pointer font-medium text-slate-600">Compare with original photo</summary>
          <img src={personPreviewUrl} alt="Original photo" className="mt-3 max-h-64 w-full rounded-lg object-contain" />
        </details>
      )}

      {authToken && (
        <button
          type="button"
          onClick={() => void handleSaveToAccount()}
          disabled={isBusy || saved}
          className="min-h-11 rounded-xl border border-dashed border-indigo-300 bg-indigo-50 px-4 py-2.5 text-sm font-medium text-indigo-700 disabled:opacity-60"
        >
          {saved ? "✓ Saved to your account" : "Save to my account"}
        </button>
      )}

      {actionMessage && <p className="text-center text-sm text-slate-500">{actionMessage}</p>}

      <div className="grid grid-cols-2 gap-3">
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={isBusy}
          className="min-h-11 rounded-full border-2 border-indigo-600 px-4 py-3 font-semibold text-indigo-600 active:scale-[0.98] disabled:opacity-50"
        >
          Save
        </button>
        <button
          type="button"
          onClick={() => void handleShare()}
          disabled={isBusy}
          className="min-h-11 rounded-full bg-indigo-600 px-4 py-3 font-semibold text-white active:scale-[0.98] disabled:opacity-50"
        >
          Share
        </button>
        <button
          type="button"
          onClick={onChangeClothing}
          className="min-h-11 rounded-full border border-slate-200 bg-white px-4 py-3 font-medium text-slate-700 active:scale-[0.98]"
        >
          Change Clothing
        </button>
        <button
          type="button"
          onClick={onTryAnother}
          className="min-h-11 rounded-full border border-slate-200 bg-white px-4 py-3 font-medium text-slate-700 active:scale-[0.98]"
        >
          Try Another
        </button>
      </div>
    </div>
  );
}
