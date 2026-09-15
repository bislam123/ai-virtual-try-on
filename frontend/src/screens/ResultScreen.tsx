import { useEffect, useState } from "react";
import { ApiError, saveTryOnResult } from "../api/tryOnClient";
import { downloadResultImage, shareResultImage } from "../utils/resultActions";

interface ResultScreenProps {
  personImage: File;
  jobId: string;
  resultUrl: string;
  saved: boolean;
  authToken: string | null;
  onSaved: () => void;
  onTryAnother: () => void;
  onChangeClothing: () => void;
}

export default function ResultScreen({
  personImage,
  jobId,
  resultUrl,
  saved,
  authToken,
  onSaved,
  onTryAnother,
  onChangeClothing,
}: ResultScreenProps) {
  const [personPreviewUrl, setPersonPreviewUrl] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  // See the identical pattern (and why it's an effect, not derived state) in
  // components/PhotoPicker.tsx.
  useEffect(() => {
    const url = URL.createObjectURL(personImage);
    setPersonPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [personImage]);

  const handleSave = async () => {
    setIsBusy(true);
    setActionMessage(null);
    try {
      await downloadResultImage(resultUrl);
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
      const outcome = await shareResultImage(resultUrl);
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
        <img src={resultUrl} alt="Try-on result" className="w-full rounded-xl object-contain" />
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
