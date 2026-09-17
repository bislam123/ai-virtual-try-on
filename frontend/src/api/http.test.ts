import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { apiFetch, setSessionExpiredListener } from "./http";

describe("apiFetch — session-expired listener", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    setSessionExpiredListener(null); // never leak a listener into another test
  });

  it("fires the listener on a 401 response when a token was used", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ detail: "Please sign in to use this feature." }), { status: 401 }));
    const listener = vi.fn();
    setSessionExpiredListener(listener);

    await expect(apiFetch("/api/auth/me", undefined, "stale-token")).rejects.toMatchObject({ status: 401 });

    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("does NOT fire the listener on a 401 response when no token was used", async () => {
    // e.g. login's own wrong-password/unknown-email 401 -- never
    // token-bearing, must never be mistaken for a revoked session.
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ detail: "Incorrect email or password." }), { status: 401 }));
    const listener = vi.fn();
    setSessionExpiredListener(listener);

    await expect(apiFetch("/api/auth/login", { method: "POST" })).rejects.toMatchObject({ status: 401 });

    expect(listener).not.toHaveBeenCalled();
  });

  it("does NOT fire the listener on a non-401 error even when a token was used", async () => {
    // e.g. delete-account's wrong-confirmation-password response (403) --
    // the token itself is still perfectly valid.
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ detail: "Incorrect email or password." }), { status: 403 }));
    const listener = vi.fn();
    setSessionExpiredListener(listener);

    await expect(apiFetch("/api/auth/me", { method: "DELETE" }, "valid-token")).rejects.toMatchObject({
      status: 403,
    });

    expect(listener).not.toHaveBeenCalled();
  });

  it("does NOT fire the listener on a successful (2xx) response", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    const listener = vi.fn();
    setSessionExpiredListener(listener);

    await apiFetch("/api/auth/me", undefined, "valid-token");

    expect(listener).not.toHaveBeenCalled();
  });

  it("never retries the request -- exactly one fetch call regardless of a 401", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ detail: "nope" }), { status: 401 }));
    setSessionExpiredListener(vi.fn());

    await expect(apiFetch("/api/auth/me", undefined, "stale-token")).rejects.toBeTruthy();

    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it("does not throw when no listener is registered", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ detail: "nope" }), { status: 401 }));
    setSessionExpiredListener(null);

    await expect(apiFetch("/api/auth/me", undefined, "stale-token")).rejects.toMatchObject({ status: 401 });
  });
});
