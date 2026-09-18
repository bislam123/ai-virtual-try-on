import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminScreen from "./AdminScreen";
import { ApiError } from "../api/http";
import * as adminClient from "../api/adminClient";

vi.mock("../api/adminClient");
const mockedAdminClient = vi.mocked(adminClient);

const dashboardData = {
  total_users: 5,
  active_users: 4,
  jobs_by_status: { completed: 3, failed: 1 },
  recent_failed_jobs: [
    {
      job_id: "job-fail-1",
      user_id: 2,
      status: "failed" as const,
      category: "tops",
      garment_photo_type: "flat-lay",
      error: "We couldn't generate your try-on result.",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:05:00Z",
      processing_duration_seconds: 300,
    },
  ],
  active_job_count: 1,
  max_active_job_capacity: 10,
  avg_processing_duration_seconds: 42.5,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedAdminClient.getAdminDashboard.mockResolvedValue(dashboardData);
  mockedAdminClient.listAdminUsers.mockResolvedValue({ users: [], total: 0 });
  mockedAdminClient.listAdminJobs.mockResolvedValue({ jobs: [], total: 0 });
  mockedAdminClient.listAdminPlans.mockResolvedValue([]);
});

describe("AdminScreen — navigation", () => {
  it("calls onClose when Back to app is clicked", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: "Back to app" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("defaults to the Dashboard tab and switches tabs on click", async () => {
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await waitFor(() => expect(mockedAdminClient.getAdminDashboard).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "dashboard" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "users" }));

    expect(screen.getByRole("button", { name: "users" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "dashboard" })).toHaveAttribute("aria-pressed", "false");
    await waitFor(() => expect(mockedAdminClient.listAdminUsers).toHaveBeenCalled());
  });
});

describe("AdminScreen — dashboard tab", () => {
  it("shows a loading status, then real stats and recent failures", async () => {
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading dashboard/i);

    await waitFor(() => expect(screen.getByText("5")).toBeInTheDocument()); // total_users
    expect(screen.getByText("4")).toBeInTheDocument(); // active_users
    expect(screen.getByText("1 / 10")).toBeInTheDocument(); // active_job_count / capacity
    expect(screen.getByText("43s")).toBeInTheDocument(); // rounded avg duration
    expect(screen.getByText(/completed: 3/)).toBeInTheDocument();
    expect(screen.getByText(/failed: 1/)).toBeInTheDocument();
    expect(screen.getByText("job-fail-1")).toBeInTheDocument();
  });

  it("shows an error alert with the backend's real message on failure (e.g. a demoted/expired admin)", async () => {
    mockedAdminClient.getAdminDashboard.mockRejectedValue(new ApiError("Admin access required.", 403));
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Admin access required."));
  });

  it("shows an em-dash, never a fabricated number, when no completed jobs exist yet to average", async () => {
    mockedAdminClient.getAdminDashboard.mockResolvedValue({ ...dashboardData, avg_processing_duration_seconds: null });
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("—")).toBeInTheDocument());
  });
});

describe("AdminScreen — users tab", () => {
  const users = [
    { id: 1, email: "alice@example.com", plan: "free", created_at: "2026-01-01T00:00:00Z", is_admin: false, is_active: true },
    { id: 2, email: "bob@example.com", plan: "premium", created_at: "2026-01-02T00:00:00Z", is_admin: true, is_active: false },
  ];

  it("shows an empty state when there are no users", async () => {
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "users" }));

    await waitFor(() => expect(screen.getByText("No users found.")).toBeInTheDocument());
  });

  it("renders each user's email/plan/admin/active status, unlabelled table cells match", async () => {
    mockedAdminClient.listAdminUsers.mockResolvedValue({ users, total: 2 });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "users" }));

    await screen.findByText("alice@example.com");
    expect(screen.getByText("bob@example.com")).toBeInTheDocument();
    expect(screen.getByText("premium")).toBeInTheDocument();
    expect(screen.getByText("Disabled")).toBeInTheDocument(); // bob is inactive
    expect(screen.getByText("Showing 2 of 2")).toBeInTheDocument();
  });

  it("lets an admin disable an active user, and the row updates to reflect it", async () => {
    mockedAdminClient.listAdminUsers.mockResolvedValue({ users: [users[0]], total: 1 });
    mockedAdminClient.disableAdminUser.mockResolvedValue({ ...users[0], is_active: false });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "users" }));
    await screen.findByText("alice@example.com");

    const row = screen.getByText("alice@example.com").closest("tr");
    expect(row).not.toBeNull();
    await user.click(within(row as HTMLElement).getByRole("button", { name: "Disable" }));

    await waitFor(() => expect(mockedAdminClient.disableAdminUser).toHaveBeenCalledWith(1, "tok"));
    await waitFor(() => {
      const updatedRow = screen.getByText("alice@example.com").closest("tr") as HTMLElement;
      expect(within(updatedRow).getByRole("button", { name: "Enable" })).toBeInTheDocument();
    });
  });

  it("shows an action error without losing the list on a failed disable attempt", async () => {
    mockedAdminClient.listAdminUsers.mockResolvedValue({ users: [users[0]], total: 1 });
    mockedAdminClient.disableAdminUser.mockRejectedValue(
      new ApiError("You can't disable your own admin account.", 400),
    );
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "users" }));
    await screen.findByText("alice@example.com");

    await user.click(screen.getByRole("button", { name: "Disable" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("You can't disable your own admin account."),
    );
    expect(screen.getByText("alice@example.com")).toBeInTheDocument();
  });

  it("submits the search box's value as the search param", async () => {
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "users" }));
    await waitFor(() =>
      expect(mockedAdminClient.listAdminUsers).toHaveBeenCalledWith("tok", { search: undefined, limit: 50 }),
    );

    await user.type(screen.getByLabelText("Search users by email"), "alice");
    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() =>
      expect(mockedAdminClient.listAdminUsers).toHaveBeenLastCalledWith("tok", { search: "alice", limit: 50 }),
    );
  });
});

describe("AdminScreen — jobs tab", () => {
  it("shows an empty state and re-fetches when the status filter changes", async () => {
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "jobs" }));

    await waitFor(() => expect(screen.getByText("No jobs found.")).toBeInTheDocument());

    await user.selectOptions(screen.getByLabelText("Status"), "failed");

    await waitFor(() =>
      expect(mockedAdminClient.listAdminJobs).toHaveBeenLastCalledWith("tok", { status: "failed", limit: 50 }),
    );
  });

  it("renders job rows with user_id (not email) and a computed duration", async () => {
    mockedAdminClient.listAdminJobs.mockResolvedValue({
      jobs: [
        {
          job_id: "abcdefgh12345678",
          user_id: 9,
          status: "completed",
          category: "tops",
          garment_photo_type: "flat-lay",
          error: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:05:00Z",
          processing_duration_seconds: 300,
        },
      ],
      total: 1,
    });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "jobs" }));

    await screen.findByText("9");
    expect(screen.getByText("300s")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("shows an anonymous job's user column as 'Anonymous', not null/blank", async () => {
    mockedAdminClient.listAdminJobs.mockResolvedValue({
      jobs: [
        {
          job_id: "anon-job-1",
          user_id: null,
          status: "pending",
          category: "tops",
          garment_photo_type: "flat-lay",
          error: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          processing_duration_seconds: null,
        },
      ],
      total: 1,
    });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "jobs" }));

    await screen.findByText("Anonymous");
  });
});

describe("AdminScreen — plans tab", () => {
  it("shows an empty state when no plans are configured", async () => {
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "plans" }));

    await waitFor(() => expect(screen.getByText("No plans configured.")).toBeInTheDocument());
  });

  it("shows plan limits, using 'Unlimited' for a null cap", async () => {
    mockedAdminClient.listAdminPlans.mockResolvedValue([
      { name: "free", max_generations_per_day: 5, max_generations_per_month: null, max_num_timesteps: 30 },
    ]);
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "plans" }));

    await screen.findByText("free");
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("Unlimited")).toBeInTheDocument();
  });
});
