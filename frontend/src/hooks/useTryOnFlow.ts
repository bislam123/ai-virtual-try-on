import { useCallback, useRef, useState } from "react";
import { ApiError, cancelTryOnJob, getJobStatus, getResultImageUrl, submitTryOnJob } from "../api/tryOnClient";
import type { GarmentCategory } from "../types/tryOn";

interface IdempotencyKeyCache {
  key: string;
  personImage: File;
  garmentImage: File;
  category: GarmentCategory;
}

const POLL_INTERVAL_MS = 4000;

// Bounds how long this tab will keep actively polling one job, so an
// abandoned/forgotten tab doesn't poll forever. Generous, not arbitrary:
// docs/DEVELOPMENT.md documents this CPU-only dev machine's *legitimate*
// worst-case generation time as 10-70+ minutes, and the backend's own
// timeouts (AITRYON_INFERENCE_TIMEOUT_SECONDS / AITRYON_PROVIDER_LOCK_ACQUIRE_TIMEOUT_SECONDS,
// each up to an hour by default) mean a legitimately queued job can take
// even longer in the worst case. Hitting this bound stops *this tab's*
// polling loop -- it never claims the job itself failed (see
// POLL_TIMEOUT_MESSAGE below), since it may well still complete.
const MAX_POLL_DURATION_MS = 90 * 60 * 1000; // 90 minutes

const POLL_TIMEOUT_MESSAGE =
  "This is taking longer than expected. Your try-on may still complete — please check back later.";
const CANCELLED_MESSAGE = "This request was cancelled.";

type Submission =
  | { status: "idle" }
  | { status: "pending" | "processing"; jobId: string }
  | { status: "completed"; jobId: string; resultUrl: string; saved: boolean }
  | { status: "failed"; message: string };

export function useTryOnFlow() {
  const [personImage, setPersonImage] = useState<File | null>(null);
  const [garmentImage, setGarmentImage] = useState<File | null>(null);
  const [category, setCategory] = useState<GarmentCategory>("tops");
  const [submission, setSubmission] = useState<Submission>({ status: "idle" });
  const pollTimeoutRef = useRef<number | null>(null);
  const idempotencyKeyCacheRef = useRef<IdempotencyKeyCache | null>(null);

  // A double tap (or a browser/mobile-network-level retry of an in-flight
  // request) must send the *same* Idempotency-Key so the backend can
  // collapse them into one job -- see api/tryon.py's Idempotency-Key
  // handling. A ref (not state) is what makes this safe: its mutation is
  // synchronous and visible immediately to a second call arriving before
  // any re-render, unlike state, which batches. A genuinely new photo/
  // garment/category selection produces a new File/category, so the cache
  // naturally misses and a fresh key is generated -- that's a new request,
  // not a duplicate, and must not reuse the old job.
  const getIdempotencyKey = useCallback((person: File, garment: File, cat: GarmentCategory): string => {
    const cached = idempotencyKeyCacheRef.current;
    if (cached && cached.personImage === person && cached.garmentImage === garment && cached.category === cat) {
      return cached.key;
    }
    const key = crypto.randomUUID();
    idempotencyKeyCacheRef.current = { key, personImage: person, garmentImage: garment, category: cat };
    return key;
  }, []);

  const stopPolling = useCallback(() => {
    if (pollTimeoutRef.current !== null) {
      window.clearTimeout(pollTimeoutRef.current);
      pollTimeoutRef.current = null;
    }
  }, []);

  const poll = useCallback(
    (jobId: string, token?: string | null) => {
      // Tracked once per poll() call (not reset each tick), so the bound
      // covers the whole polling session's real elapsed time.
      const pollStartedAt = Date.now();

      const tick = async () => {
        try {
          const status = await getJobStatus(jobId, token);
          if (status.status === "completed") {
            setSubmission({ status: "completed", jobId, resultUrl: getResultImageUrl(jobId), saved: status.saved });
            return;
          }
          if (status.status === "failed") {
            setSubmission({
              status: "failed",
              message: status.error ?? "We couldn't generate your try-on result. Please try again.",
            });
            return;
          }
          if (status.status === "cancelled") {
            setSubmission({ status: "failed", message: CANCELLED_MESSAGE });
            return;
          }
          // Only "pending" | "processing" can reach here.
          if (Date.now() - pollStartedAt > MAX_POLL_DURATION_MS) {
            setSubmission({ status: "failed", message: POLL_TIMEOUT_MESSAGE });
            return;
          }
          setSubmission({ status: status.status, jobId });
          pollTimeoutRef.current = window.setTimeout(tick, POLL_INTERVAL_MS);
        } catch (err) {
          setSubmission({
            status: "failed",
            message: err instanceof ApiError ? err.message : "Something went wrong. Please try again.",
          });
        }
      };
      void tick();
    },
    [],
  );

  const submit = useCallback(
    async (token?: string | null) => {
      if (!personImage || !garmentImage) return;
      const idempotencyKey = getIdempotencyKey(personImage, garmentImage, category);
      setSubmission({ status: "pending", jobId: "" });
      try {
        // Signing in is optional — an anonymous submission works exactly as
        // before. An authenticated one just gets attributed to that account,
        // which is what enables "Save to my account" on the result screen.
        const created = await submitTryOnJob(personImage, garmentImage, category, token, idempotencyKey);
        // In practice the backend always returns "pending" here — jobs run in
        // the background, not synchronously — but narrow properly rather than
        // assume, so this stays correct if that ever changes.
        if (created.status === "failed") {
          setSubmission({ status: "failed", message: "We couldn't generate your try-on result. Please try again." });
          return;
        }
        if (created.status === "completed") {
          setSubmission({
            status: "completed",
            jobId: created.job_id,
            resultUrl: getResultImageUrl(created.job_id),
            saved: false,
          });
          return;
        }
        if (created.status === "cancelled") {
          // Cannot actually happen -- a job is never created already
          // cancelled -- but narrowed explicitly rather than assumed away,
          // same reasoning as the comment above.
          setSubmission({ status: "failed", message: CANCELLED_MESSAGE });
          return;
        }
        setSubmission({ status: created.status, jobId: created.job_id });
        poll(created.job_id, token);
      } catch (err) {
        setSubmission({
          status: "failed",
          message: err instanceof ApiError ? err.message : "Something went wrong. Please try again.",
        });
      }
    },
    [personImage, garmentImage, category, poll, getIdempotencyKey],
  );

  const dismissError = useCallback(() => {
    setSubmission({ status: "idle" });
  }, []);

  // Only ever meaningfully cancels a job that's still "pending" -- the
  // backend refuses (409) once generation has actually started, since
  // this architecture can't safely stop an in-flight inference (see
  // backend/app/services/tryon_service.py's cancel_job). On that refusal,
  // resumes polling rather than leaving the screen stuck showing neither
  // progress nor an error -- the job is still genuinely in flight either
  // way, whether or not this specific cancel attempt landed in time.
  const cancel = useCallback(
    async (token?: string | null) => {
      if (submission.status !== "pending" && submission.status !== "processing") return;
      const jobId = submission.jobId;
      stopPolling();
      try {
        await cancelTryOnJob(jobId, token);
        setSubmission({ status: "failed", message: CANCELLED_MESSAGE });
      } catch {
        setSubmission({ status: submission.status, jobId });
        poll(jobId, token);
      }
    },
    [submission, stopPolling, poll],
  );

  const markSaved = useCallback(() => {
    setSubmission((prev) => (prev.status === "completed" ? { ...prev, saved: true } : prev));
  }, []);

  const tryAnother = useCallback(() => {
    stopPolling();
    setPersonImage(null);
    setGarmentImage(null);
    setCategory("tops");
    setSubmission({ status: "idle" });
  }, [stopPolling]);

  const changeClothing = useCallback(() => {
    stopPolling();
    setGarmentImage(null);
    setSubmission({ status: "idle" });
  }, [stopPolling]);

  return {
    personImage,
    setPersonImage,
    garmentImage,
    setGarmentImage,
    category,
    setCategory,
    submission,
    submit,
    cancel,
    dismissError,
    markSaved,
    tryAnother,
    changeClothing,
  };
}
