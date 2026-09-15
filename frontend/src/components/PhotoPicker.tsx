import { useEffect, useRef, useState, type ReactNode } from "react";

// Mirrors backend/app/config.py's defaults (max_upload_size_mb, and the
// JPEG/PNG/WebP allow-list enforced server-side in app/core/validation.py).
// This is just an instant, friendly first check — the backend is still the
// real source of truth and re-validates everything itself.
const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024;

interface PickerButtonConfig {
  label: string;
  /** Set to jump straight to the camera on mobile; omit to let the OS offer camera+gallery. */
  capture?: "user" | "environment";
}

interface PhotoPickerProps {
  title: string;
  file: File | null;
  onChange: (file: File | null) => void;
  buttons: PickerButtonConfig[];
  previewAlt: string;
}

function PickerButton({
  config,
  onFile,
  onError,
}: {
  config: PickerButtonConfig;
  onFile: (file: File) => void;
  onError: (message: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        className="min-h-11 flex-1 min-w-0 rounded-xl border-2 border-indigo-200 bg-white px-4 py-3 text-sm font-semibold text-indigo-700 shadow-sm active:scale-[0.98] transition hover:border-indigo-400"
      >
        {config.label}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        capture={config.capture}
        className="hidden"
        onChange={(e) => {
          const selected = e.target.files?.[0];
          e.target.value = ""; // allow picking the same file again later
          if (!selected) return;
          if (!selected.type.startsWith("image/")) {
            onError("That doesn't look like an image. Please choose a JPEG, PNG, or WebP photo.");
            return;
          }
          if (selected.size > MAX_FILE_SIZE_BYTES) {
            onError("That photo is too large. Please choose one under 10MB.");
            return;
          }
          onFile(selected);
        }}
      />
    </>
  );
}

export default function PhotoPicker({ title, file, onChange, buttons, previewAlt }: PhotoPickerProps) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Deliberately an effect, not derived-during-render state: createObjectURL
  // allocates a real browser resource that must be revoked on cleanup —
  // exactly the external-system-synchronization case effects are for.
  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  return (
    <div>
      <p className="mb-2 text-sm font-medium text-slate-700">{title}</p>

      {previewUrl ? (
        <div className="relative overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
          <img src={previewUrl} alt={previewAlt} className="max-h-64 w-full object-contain" />
          <button
            type="button"
            onClick={() => onChange(null)}
            aria-label={`Remove ${previewAlt}`}
            className="absolute right-2 top-2 flex h-11 w-11 items-center justify-center rounded-full bg-black/60 text-white active:scale-95"
          >
            ✕
          </button>
        </div>
      ) : (
        <div className="flex gap-3">
          {buttons.map((config): ReactNode => (
            <PickerButton
              key={config.label}
              config={config}
              onFile={(f) => {
                setError(null);
                onChange(f);
              }}
              onError={setError}
            />
          ))}
        </div>
      )}

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
    </div>
  );
}
