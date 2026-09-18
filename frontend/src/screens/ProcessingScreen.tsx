import { useEffect, useState } from "react";

const LATER_STAGES = ["Detecting clothing...", "Generating your try-on...", "Improving the result..."];
const STAGE_CYCLE_MS = 6000;

interface ProcessingScreenProps {
  status: "pending" | "processing";
  onCancel: () => void;
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export default function ProcessingScreen({ status, onCancel }: ProcessingScreenProps) {
  // Lazy useState initializer, not a bare Date.now() call during render: it
  // runs exactly once, at mount, which is what we want for a start time.
  const [startedAt] = useState(() => Date.now());
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [stageIndex, setStageIndex] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  useEffect(() => {
    if (status !== "processing") return;
    // We only get a single "processing" status from the backend today (no
    // granular sub-progress), so this cycles through likely stages for
    // reassurance rather than claiming to track exact progress.
    const cycle = window.setInterval(() => {
      setStageIndex((i) => (i + 1) % LATER_STAGES.length);
    }, STAGE_CYCLE_MS);
    return () => window.clearInterval(cycle);
  }, [status]);

  const currentLabel = status === "pending" ? "Preparing your photos..." : LATER_STAGES[stageIndex];

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-6 px-6 text-center">
      <div className="h-14 w-14 animate-spin rounded-full border-4 border-indigo-200 border-t-indigo-600" />
      <div>
        {/* aria-live scoped to just this label, not the whole block: it
            only changes on a meaningful stage transition (every 6s, or
            pending->processing) -- the elapsed-time counter right below
            ticks every second and must stay out of any live region, or a
            screen reader would announce it every single second. */}
        <p aria-live="polite" className="text-lg font-semibold text-slate-800">
          {currentLabel}
        </p>
        <p className="mt-1 text-sm text-slate-500">{formatElapsed(elapsedSeconds)} elapsed</p>
      </div>
      <p className="max-w-xs text-xs text-slate-500">
        This can take a few minutes. Feel free to keep this tab open — we'll show your result as soon as it's ready.
      </p>
      <button
        type="button"
        onClick={onCancel}
        className="flex min-h-11 items-center px-4 text-sm font-medium text-slate-500 active:scale-[0.98]"
      >
        Cancel
      </button>
    </div>
  );
}
