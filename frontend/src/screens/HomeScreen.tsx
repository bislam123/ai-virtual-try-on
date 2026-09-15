import PhotoPicker from "../components/PhotoPicker";
import type { GarmentCategory } from "../types/tryOn";

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
}: HomeScreenProps) {
  const canSubmit = personImage !== null && garmentImage !== null;

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col gap-6 px-4 pb-10 pt-8">
      <header className="text-center">
        <h1 className="text-3xl font-bold text-slate-900">✨ Try It On</h1>
        <p className="mt-1 text-sm text-slate-500">See how clothes look on you</p>
      </header>

      {errorMessage && (
        <div className="flex items-start justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <p>{errorMessage}</p>
          <button type="button" onClick={onDismissError} aria-label="Dismiss" className="shrink-0 font-bold">
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
          onChange={setGarmentImage}
          previewAlt="Selected clothing photo"
          buttons={[{ label: "Upload Clothing" }]}
        />

        {garmentImage && (
          <div>
            <p className="mb-2 text-sm font-medium text-slate-700">What kind of clothing is this?</p>
            <div className="flex gap-2">
              {CATEGORY_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setCategory(opt.value)}
                  className={`flex-1 rounded-lg border px-2 py-2 text-xs font-semibold transition ${
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

      <p className="text-center text-xs text-slate-400">
        Your photos are used only to generate this result and aren't kept unless you choose to save it.
      </p>
    </div>
  );
}
