import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { deleteAccount, forgotPassword, resetPassword } from "./authClient";
import { ApiError } from "./http";

describe("deleteAccount", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("sends a DELETE request with the password and bearer token, and resolves without parsing a body", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(null, { status: 204 }));

    await expect(deleteAccount("my-password", "tok-123")).resolves.toBeUndefined();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("/api/auth/me");
    expect(init?.method).toBe("DELETE");
    expect(JSON.parse(init?.body as string)).toEqual({ password: "my-password" });
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer tok-123");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("surfaces a wrong-password (401) response as an ApiError with the backend's message", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Incorrect email or password." }), { status: 401 }),
    );

    await expect(deleteAccount("wrong-password", "tok-123")).rejects.toMatchObject({
      message: "Incorrect email or password.",
      status: 401,
    });
    await expect(deleteAccount("wrong-password", "tok-123")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("forgotPassword", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("posts the email and resolves with the backend's generic message", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ message: "If an account exists for that email, we've sent a link." }), {
        status: 200,
      }),
    );

    await expect(forgotPassword("person@example.com")).resolves.toEqual({
      message: "If an account exists for that email, we've sent a link.",
    });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("/api/auth/forgot-password");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ email: "person@example.com" });
    // No Authorization header -- this endpoint is reachable while signed out.
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBeNull();
  });

  it("surfaces a rate-limited (429) response as an ApiError", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Too many attempts. Please try again in about 60 seconds." }), {
        status: 429,
      }),
    );

    await expect(forgotPassword("person@example.com")).rejects.toMatchObject({ status: 429 });
  });
});

describe("resetPassword", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("posts the token and new password under the backend's expected field names", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ message: "Your password has been reset." }), { status: 200 }),
    );

    await expect(resetPassword("raw-token-123", "new-password-456")).resolves.toEqual({
      message: "Your password has been reset.",
    });

    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("/api/auth/reset-password");
    expect(JSON.parse(init?.body as string)).toEqual({ token: "raw-token-123", new_password: "new-password-456" });
  });

  it("surfaces an invalid/expired token (400) response as an ApiError with the backend's generic message", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: "This password reset link is invalid or has expired." }), {
        status: 400,
      }),
    );

    await expect(resetPassword("bad-token", "new-password-456")).rejects.toMatchObject({
      message: "This password reset link is invalid or has expired.",
      status: 400,
    });
    await expect(resetPassword("bad-token", "new-password-456")).rejects.toBeInstanceOf(ApiError);
  });
});
