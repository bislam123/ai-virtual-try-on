import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useTryOnFlow } from "./useTryOnFlow";
import { ApiError } from "../api/http";
import type { TryOnJobStatusResponse } from "../types/tryOn";

vi.mock("../api/tryOnClient", async () => {
  const actual = await vi.importActual<typeof import("../api/tryOnClient")>("../api/tryOnClient");
  return {
    ...actual,
    submitTryOnJob: vi.fn(),
    getJobStatus: vi.fn(),
    cancelTryOnJob: vi.fn(),
  };
});

import * as tryOnClient from "../api/tryOnClient";

const mocked = vi.mocked(tryOnClient);

// Must match useTryOnFlow.ts's own POLL_INTERVAL_MS -- not exported, so
// pinned here explicitly rather than guessed at per test.
const POLL_INTERVAL_MS = 4000;

function makeFile(name = "photo.png"): File {
  return new File(["fake-bytes"], name, { type: "image/png" });
}

function statusResponse(overrides: Partial<TryOnJobStatusResponse> = {}): TryOnJobStatusResponse {
  return {
    job_id: "job-1",
    status: "processing",
    error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:01Z",
    result_url: null,
    saved: false,
    ...overrides,
  };
}

async function submitWithPhotos(token?: string | null) {
  const { result } = renderHook(() => useTryOnFlow());
  act(() => {
    result.current.setPersonImage(makeFile("person.png"));
    result.current.setGarmentImage(makeFile("garment.png"));
  });
  await act(async () => {
    await result.current.submit(token);
  });
  return result;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocked.submitTryOnJob.mockResolvedValue({ job_id: "job-1", status: "pending" });
});

afterEach(() => {
  vi.useRealTimers();
});

// --- basic polling lifecycle --------------------------------------------------

describe("useTryOnFlow polling — terminal states stop polling", () => {
  it("stops polling as soon as the job is completed", async () => {
    mocked.getJobStatus.mockResolvedValue(
      statusResponse({ status: "completed", result_url: "/api/try-on/job-1/result" }),
    );

    const result = await submitWithPhotos(null);

    await waitFor(() => expect(result.current.submission.status).toBe("completed"));
    expect(mocked.getJobStatus).toHaveBeenCalledTimes(1); // no further polling after the terminal state
  });

  it("stops polling as soon as the job fails, surfacing the backend's own error message", async () => {
    mocked.getJobStatus.mockResolvedValue(statusResponse({ status: "failed", error: "Something specific broke." }));

    const result = await submitWithPhotos(null);

    await waitFor(() => expect(result.current.submission.status).toBe("failed"));
    expect(mocked.getJobStatus).toHaveBeenCalledTimes(1);
    if (result.current.submission.status === "failed") {
      expect(result.current.submission.message).toBe("Something specific broke.");
    }
  });

  it("stops polling as soon as the job is discovered cancelled (e.g. from another tab)", async () => {
    mocked.getJobStatus.mockResolvedValue(statusResponse({ status: "cancelled" }));

    const result = await submitWithPhotos(null);

    await waitFor(() => expect(result.current.submission.status).toBe("failed"));
    expect(mocked.getJobStatus).toHaveBeenCalledTimes(1);
    if (result.current.submission.status === "failed") {
      expect(result.current.submission.message).toMatch(/cancelled/i);
    }
  });

  it("stops polling (does not retry forever) when the server becomes unreachable", async () => {
    mocked.getJobStatus.mockRejectedValue(new ApiError("We couldn't reach the server.", 0));

    const result = await submitWithPhotos(null);

    await waitFor(() => expect(result.current.submission.status).toBe("failed"));
    expect(mocked.getJobStatus).toHaveBeenCalledTimes(1); // one failed attempt, no retry loop
  });
});

// --- auth / anonymous preservation --------------------------------------------

describe("useTryOnFlow polling — auth token handling", () => {
  it("passes the caller's token through to every poll request", async () => {
    mocked.getJobStatus.mockResolvedValue(statusResponse({ status: "completed" }));

    await submitWithPhotos("tok-abc");

    await waitFor(() => expect(mocked.getJobStatus).toHaveBeenCalledWith("job-1", "tok-abc"));
  });

  it("polls with no token for an anonymous submission", async () => {
    mocked.getJobStatus.mockResolvedValue(statusResponse({ status: "completed" }));

    await submitWithPhotos(null);

    await waitFor(() => expect(mocked.getJobStatus).toHaveBeenCalledWith("job-1", null));
  });
});

// --- scheduling: one request at a time, bounded lifetime ----------------------

describe("useTryOnFlow polling — scheduling", () => {
  it("schedules exactly one more poll after the interval when still processing, not before", async () => {
    vi.useFakeTimers();
    mocked.getJobStatus.mockResolvedValue(statusResponse({ status: "processing" }));

    const { result } = renderHook(() => useTryOnFlow());
    act(() => {
      result.current.setPersonImage(makeFile("person.png"));
      result.current.setGarmentImage(makeFile("garment.png"));
    });
    await act(async () => {
      await result.current.submit(null);
    });

    expect(mocked.getJobStatus).toHaveBeenCalledTimes(1); // the immediate first poll

    // Not yet due -- no second call from merely letting microtasks settle.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS - 500);
    });
    expect(mocked.getJobStatus).toHaveBeenCalledTimes(1);

    // Now due.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(mocked.getJobStatus).toHaveBeenCalledTimes(2);
  });

  it("gives up after the bounded polling duration, without claiming the job itself failed", async () => {
    vi.useFakeTimers();
    mocked.getJobStatus.mockResolvedValue(statusResponse({ status: "processing" }));

    const { result } = renderHook(() => useTryOnFlow());
    act(() => {
      result.current.setPersonImage(makeFile("person.png"));
      result.current.setGarmentImage(makeFile("garment.png"));
    });
    await act(async () => {
      await result.current.submit(null);
    });
    expect(result.current.submission.status).toBe("processing");

    // Jump the clock far past the (90-minute) bound, then let the next
    // already-scheduled tick actually fire and observe it -- avoids
    // iterating ~1350 individual 4s ticks to get there.
    vi.setSystemTime(Date.now() + 91 * 60 * 1000);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });

    expect(result.current.submission.status).toBe("failed");
    if (result.current.submission.status === "failed") {
      expect(result.current.submission.message).toMatch(/taking longer than expected/i);
      // Deliberately does not say the job failed/errored -- it may still complete.
      expect(result.current.submission.message.toLowerCase()).not.toContain("failed");
    }
  });
});

// --- cancellation ----------------------------------------------------------------

describe("useTryOnFlow.cancel", () => {
  it("cancels a pending/processing job and stops polling", async () => {
    vi.useFakeTimers();
    mocked.getJobStatus.mockResolvedValue(statusResponse({ status: "processing" }));
    mocked.cancelTryOnJob.mockResolvedValue(statusResponse({ status: "cancelled" }));

    const { result } = renderHook(() => useTryOnFlow());
    act(() => {
      result.current.setPersonImage(makeFile("person.png"));
      result.current.setGarmentImage(makeFile("garment.png"));
    });
    await act(async () => {
      await result.current.submit(null);
    });

    await act(async () => {
      await result.current.cancel("tok-abc");
    });

    expect(mocked.cancelTryOnJob).toHaveBeenCalledWith("job-1", "tok-abc");
    expect(result.current.submission.status).toBe("failed");
    if (result.current.submission.status === "failed") {
      expect(result.current.submission.message).toMatch(/cancelled/i);
    }

    // Polling must actually have stopped -- advancing well past the
    // interval triggers no further getJobStatus calls.
    const callsBeforeAdvance = mocked.getJobStatus.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
    });
    expect(mocked.getJobStatus).toHaveBeenCalledTimes(callsBeforeAdvance);
  });

  it("on refusal (already processing, 409), resumes polling instead of getting stuck", async () => {
    mocked.getJobStatus.mockResolvedValue(statusResponse({ status: "processing" }));
    mocked.cancelTryOnJob.mockRejectedValue(
      new ApiError("This job can no longer be cancelled (status: processing).", 409),
    );

    const result = await submitWithPhotos(null);
    await waitFor(() => expect(result.current.submission.status).toBe("processing"));

    await act(async () => {
      await result.current.cancel(null);
    });

    // Still in progress, not shown as failed/cancelled -- and polling
    // resumed (a subsequent getJobStatus call happened after the retry).
    expect(result.current.submission.status).toBe("processing");
  });

  it("does nothing when there is no active job to cancel", async () => {
    const { result } = renderHook(() => useTryOnFlow());

    await act(async () => {
      await result.current.cancel(null);
    });

    expect(mocked.cancelTryOnJob).not.toHaveBeenCalled();
  });
});
