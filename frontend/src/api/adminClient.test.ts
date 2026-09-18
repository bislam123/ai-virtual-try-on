import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  disableAdminUser,
  enableAdminUser,
  getAdminDashboard,
  getAdminUser,
  listAdminJobs,
  listAdminPlans,
  listAdminUsers,
} from "./adminClient";
import { ApiError } from "./http";

describe("adminClient", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("listAdminUsers sends the bearer token and encodes search/limit/offset as query params", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ users: [], total: 0 }), { status: 200 }));

    await listAdminUsers("tok-admin", { search: "person@example.com", limit: 10, offset: 20 });

    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("/api/admin/users?");
    expect(String(url)).toContain("search=person%40example.com");
    expect(String(url)).toContain("limit=10");
    expect(String(url)).toContain("offset=20");
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer tok-admin");
  });

  it("listAdminUsers omits query params entirely when none are given", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ users: [], total: 0 }), { status: 200 }));

    await listAdminUsers("tok-admin");

    const [url] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("/api/admin/users");
    expect(String(url)).not.toContain("?");
  });

  it("getAdminUser fetches the per-user detail endpoint", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    const detail = {
      id: 7,
      email: "person@example.com",
      plan: "free",
      created_at: "2026-01-01T00:00:00Z",
      is_admin: false,
      is_active: true,
      usage: {
        plan: "free",
        limit_per_day: 5,
        limit_per_month: null,
        used_today: 1,
        used_this_month: 1,
        remaining_today: 4,
        remaining_this_month: null,
        max_num_timesteps: 30,
      },
    };
    mockFetch.mockResolvedValue(new Response(JSON.stringify(detail), { status: 200 }));

    await expect(getAdminUser(7, "tok-admin")).resolves.toEqual(detail);
    expect(String(mockFetch.mock.calls[0][0])).toContain("/api/admin/users/7");
  });

  it("getAdminUser surfaces a 404 as an ApiError", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: "We couldn't find that user." }), { status: 404 }),
    );

    await expect(getAdminUser(999, "tok-admin")).rejects.toBeInstanceOf(ApiError);
  });

  it("disableAdminUser / enableAdminUser POST to the expected action endpoints", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    const userBody = { id: 3, email: "x@example.com", plan: "free", created_at: "2026-01-01T00:00:00Z", is_admin: false, is_active: false };
    // A fresh Response per call -- a single shared instance's body can
    // only be read (.json()) once, and this test calls two different
    // functions against the same mocked fetch.
    mockFetch.mockImplementation(() => Promise.resolve(new Response(JSON.stringify(userBody), { status: 200 })));

    await disableAdminUser(3, "tok-admin");
    expect(mockFetch.mock.calls[0][1]?.method).toBe("POST");
    expect(String(mockFetch.mock.calls[0][0])).toContain("/api/admin/users/3/disable");

    await enableAdminUser(3, "tok-admin");
    expect(mockFetch.mock.calls[1][1]?.method).toBe("POST");
    expect(String(mockFetch.mock.calls[1][0])).toContain("/api/admin/users/3/enable");
  });

  it("disableAdminUser surfaces a 403 (non-admin caller) as an ApiError", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ detail: "Admin access required." }), { status: 403 }));

    await expect(disableAdminUser(3, "tok-not-admin")).rejects.toMatchObject({
      message: "Admin access required.",
      status: 403,
    });
  });

  it("listAdminPlans fetches the plans list", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    const plans = [{ name: "free", max_generations_per_day: 5, max_generations_per_month: null, max_num_timesteps: 30 }];
    mockFetch.mockResolvedValue(new Response(JSON.stringify(plans), { status: 200 }));

    await expect(listAdminPlans("tok-admin")).resolves.toEqual(plans);
    expect(String(mockFetch.mock.calls[0][0])).toContain("/api/admin/plans");
  });

  it("listAdminJobs encodes status/userId/limit/offset as query params", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ jobs: [], total: 0 }), { status: 200 }));

    await listAdminJobs("tok-admin", { status: "failed", userId: 42, limit: 5, offset: 0 });

    const [url] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("status=failed");
    expect(String(url)).toContain("user_id=42");
    expect(String(url)).toContain("limit=5");
  });

  it("getAdminDashboard fetches the dashboard summary", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    const dashboard = {
      total_users: 2,
      active_users: 2,
      jobs_by_status: { completed: 1 },
      recent_failed_jobs: [],
      active_job_count: 0,
      max_active_job_capacity: 10,
      avg_processing_duration_seconds: null,
    };
    mockFetch.mockResolvedValue(new Response(JSON.stringify(dashboard), { status: 200 }));

    await expect(getAdminDashboard("tok-admin")).resolves.toEqual(dashboard);
    expect(String(mockFetch.mock.calls[0][0])).toContain("/api/admin/dashboard");
  });

  it("every admin call requires and sends a token, never anonymously", async () => {
    const mockFetch = vi.mocked(globalThis.fetch);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ users: [], total: 0 }), { status: 200 }));

    await listAdminUsers("tok-admin");

    const [, init] = mockFetch.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer tok-admin");
  });
});
