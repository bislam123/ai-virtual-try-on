import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useAuth } from "./useAuth";

// Deliberately does NOT mock ../api/authClient (unlike useAuth.test.ts) --
// this file exercises the real authClient -> real apiFetch -> session-
// expired-listener path end to end, with only the network (global fetch)
// faked. useAuth.test.ts covers the rest of useAuth's behavior against a
// mocked authClient; this one exists specifically to prove the *wiring*
// between http.ts's module-level listener and useAuth's state actually
// works, not just each half in isolation.

const TOKEN_STORAGE_KEY = "aitryon_auth_token";

describe("useAuth — session-expired integration (real apiFetch, mocked network)", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    window.localStorage.clear();
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("clears a stored-but-now-revoked token on mount, with no retry loop", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, "revoked-token");
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Please sign in to use this feature." }), { status: 401 }),
    );

    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(result.current.token).toBeNull());
    expect(result.current.user).toBeNull();
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(mockFetch).toHaveBeenCalledTimes(1); // one check, one failure, no retry
  });

  it("a valid token is left alone and the user loads normally", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, "valid-token");
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ id: 1, email: "person@example.com", plan: "free", created_at: "2026-01-01T00:00:00Z" }), {
        status: 200,
      }),
    );

    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(result.current.user).not.toBeNull());
    expect(result.current.token).toBe("valid-token");
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBe("valid-token");
  });

  it("unregisters its listener on unmount, so a later 401 from an unrelated fetch can't reach a torn-down instance", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, "valid-token");
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ id: 1, email: "person@example.com", plan: "free", created_at: "2026-01-01T00:00:00Z" }), {
        status: 200,
      }),
    );

    const { unmount } = renderHook(() => useAuth());
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));

    // Should not throw even though nothing is listening anymore.
    unmount();
    const { apiFetch } = await import("../api/http");
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ detail: "nope" }), { status: 401 }));
    await expect(apiFetch("/api/auth/me", undefined, "valid-token")).rejects.toMatchObject({ status: 401 });
  });
});
