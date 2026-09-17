import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { deleteAccount } from "./authClient";
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
