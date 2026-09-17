import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cancelTryOnJob } from "./tryOnClient";
import { ApiError } from "./http";

describe("cancelTryOnJob", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("posts to the cancel endpoint with the bearer token when signed in", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(
        JSON.stringify({
          job_id: "job-1",
          status: "cancelled",
          error: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:01Z",
          result_url: null,
          saved: false,
        }),
        { status: 200 },
      ),
    );

    const result = await cancelTryOnJob("job-1", "tok-123");

    expect(result.status).toBe("cancelled");
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("/api/try-on/job-1/cancel");
    expect(init?.method).toBe("POST");
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer tok-123");
  });

  it("works without a token, for an anonymous job", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(
        JSON.stringify({
          job_id: "job-2",
          status: "cancelled",
          error: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:01Z",
          result_url: null,
          saved: false,
        }),
        { status: 200 },
      ),
    );

    await cancelTryOnJob("job-2");

    const [, init] = mockFetch.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBeNull();
  });

  it("surfaces a 409 (already processing, can't cancel) as an ApiError with the backend's message", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "This job can no longer be cancelled (status: processing). Generation may already be in progress.",
        }),
        { status: 409 },
      ),
    );

    await expect(cancelTryOnJob("job-3")).rejects.toBeInstanceOf(ApiError);
    await expect(cancelTryOnJob("job-3")).rejects.toMatchObject({ status: 409 });
  });
});
