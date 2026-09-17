import { useCallback, useRef, useState } from "react";
import { ApiError, getJobStatus, getResultImageUrl, submitTryOnJob } from "../api/tryOnClient";
import type { GarmentCategory } from "../types/tryOn";

const POLL_INTERVAL_MS = 4000;

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

  const stopPolling = useCallback(() => {
    if (pollTimeoutRef.current !== null) {
      window.clearTimeout(pollTimeoutRef.current);
      pollTimeoutRef.current = null;
    }
  }, []);

  const poll = useCallback(
    (jobId: string, token?: string | null) => {
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
      setSubmission({ status: "pending", jobId: "" });
      try {
        // Signing in is optional — an anonymous submission works exactly as
        // before. An authenticated one just gets attributed to that account,
        // which is what enables "Save to my account" on the result screen.
        const created = await submitTryOnJob(personImage, garmentImage, category, token);
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
        setSubmission({ status: created.status, jobId: created.job_id });
        poll(created.job_id, token);
      } catch (err) {
        setSubmission({
          status: "failed",
          message: err instanceof ApiError ? err.message : "Something went wrong. Please try again.",
        });
      }
    },
    [personImage, garmentImage, category, poll],
  );

  const dismissError = useCallback(() => {
    setSubmission({ status: "idle" });
  }, []);

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
    dismissError,
    markSaved,
    tryAnother,
    changeClothing,
  };
}
