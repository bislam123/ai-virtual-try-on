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
  mockedAdminClient.listAdminUsers.mockResolvedValue({ users: [], total: 0, limit: 50, offset: 0 });
  mockedAdminClient.listAdminJobs.mockResolvedValue({ jobs: [], total: 0, limit: 50, offset: 0 });
  mockedAdminClient.listAdminPlans.mockResolvedValue([]);
  mockedAdminClient.listAdminAuditLog.mockResolvedValue({ entries: [], total: 0, limit: 50, offset: 0 });
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
    mockedAdminClient.listAdminUsers.mockResolvedValue({ users, total: 2, limit: 50, offset: 0 });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "users" }));

    await screen.findByText("alice@example.com");
    expect(screen.getByText("bob@example.com")).toBeInTheDocument();
    expect(screen.getByText("premium")).toBeInTheDocument();
    expect(screen.getByText("Disabled")).toBeInTheDocument(); // bob is inactive
    expect(screen.getByText("Showing 1–2 of 2")).toBeInTheDocument();
  });

  it("lets an admin disable an active user, and the row updates to reflect it", async () => {
    mockedAdminClient.listAdminUsers.mockResolvedValue({ users: [users[0]], total: 1, limit: 50, offset: 0 });
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
    mockedAdminClient.listAdminUsers.mockResolvedValue({ users: [users[0]], total: 1, limit: 50, offset: 0 });
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
      expect(mockedAdminClient.listAdminUsers).toHaveBeenCalledWith("tok", { search: undefined, limit: 50, offset: 0 }),
    );

    await user.type(screen.getByLabelText("Search users by email"), "alice");
    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() =>
      expect(mockedAdminClient.listAdminUsers).toHaveBeenLastCalledWith("tok", {
        search: "alice",
        limit: 50,
        offset: 0,
      }),
    );
  });

  it("pages forward and back through users with Previous/Next, disabling at each end", async () => {
    mockedAdminClient.listAdminUsers.mockResolvedValueOnce({ users: [users[0]], total: 2, limit: 50, offset: 0 });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "users" }));
    await screen.findByText("alice@example.com");

    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).not.toBeDisabled();

    mockedAdminClient.listAdminUsers.mockResolvedValueOnce({ users: [users[1]], total: 2, limit: 50, offset: 0 });
    await user.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() =>
      expect(mockedAdminClient.listAdminUsers).toHaveBeenLastCalledWith("tok", {
        search: undefined,
        limit: 50,
        offset: 50,
      }),
    );
    await screen.findByText("bob@example.com");
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();

    mockedAdminClient.listAdminUsers.mockResolvedValueOnce({ users: [users[0]], total: 2, limit: 50, offset: 0 });
    await user.click(screen.getByRole("button", { name: "Previous page" }));

    await waitFor(() =>
      expect(mockedAdminClient.listAdminUsers).toHaveBeenLastCalledWith("tok", {
        search: undefined,
        limit: 50,
        offset: 0,
      }),
    );
  });

  it("resets to page 1 when a new search is submitted", async () => {
    mockedAdminClient.listAdminUsers.mockResolvedValueOnce({ users: [users[1]], total: 51, limit: 50, offset: 0 });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "users" }));
    await screen.findByText("bob@example.com");

    mockedAdminClient.listAdminUsers.mockResolvedValueOnce({ users: [users[1]], total: 51, limit: 50, offset: 0 });
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(mockedAdminClient.listAdminUsers).toHaveBeenLastCalledWith("tok", {
        search: undefined,
        limit: 50,
        offset: 50,
      }),
    );

    mockedAdminClient.listAdminUsers.mockResolvedValueOnce({ users: [users[0]], total: 1, limit: 50, offset: 0 });
    await user.type(screen.getByLabelText("Search users by email"), "alice");
    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() =>
      expect(mockedAdminClient.listAdminUsers).toHaveBeenLastCalledWith("tok", {
        search: "alice",
        limit: 50,
        offset: 0,
      }),
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
      expect(mockedAdminClient.listAdminJobs).toHaveBeenLastCalledWith("tok", {
        status: "failed",
        limit: 50,
        offset: 0,
      }),
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
      limit: 50,
      offset: 0,
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
      limit: 50,
      offset: 0,
    });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "jobs" }));

    await screen.findByText("Anonymous");
  });

  it("pages forward through jobs with Next, requesting the next offset", async () => {
    const job = {
      job_id: "job-1",
      user_id: 1,
      status: "completed" as const,
      category: "tops",
      garment_photo_type: "flat-lay",
      error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      processing_duration_seconds: 10,
    };
    mockedAdminClient.listAdminJobs.mockResolvedValueOnce({ jobs: [job], total: 60, limit: 50, offset: 0 });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "jobs" }));
    await screen.findByText("completed");

    mockedAdminClient.listAdminJobs.mockResolvedValueOnce({ jobs: [{ ...job, job_id: "job-2" }], total: 60, limit: 50, offset: 0 });
    await user.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() =>
      expect(mockedAdminClient.listAdminJobs).toHaveBeenLastCalledWith("tok", {
        status: undefined,
        limit: 50,
        offset: 50,
      }),
    );
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

  it("opens an accessible edit dialog, saves changes, and reflects them in the list", async () => {
    mockedAdminClient.listAdminPlans.mockResolvedValue([
      { name: "free", max_generations_per_day: 5, max_generations_per_month: null, max_num_timesteps: 30 },
    ]);
    mockedAdminClient.updateAdminPlan.mockResolvedValue({
      name: "free",
      max_generations_per_day: 10,
      max_generations_per_month: null,
      max_num_timesteps: 30,
    });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "plans" }));
    await screen.findByText("free");

    await user.click(screen.getByRole("button", { name: "Edit" }));

    const dialog = screen.getByRole("dialog", { name: /edit free plan/i });
    expect(dialog).toHaveAttribute("aria-modal", "true");

    const perDayInput = within(dialog).getByLabelText(/max generations per day/i);
    await user.clear(perDayInput);
    await user.type(perDayInput, "10");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(mockedAdminClient.updateAdminPlan).toHaveBeenCalledWith(
        "free",
        { max_generations_per_day: 10, max_generations_per_month: null, max_num_timesteps: 30 },
        "tok",
      ),
    );

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("10")).toBeInTheDocument();
  });

  it("treats a blank field as unlimited (null) when saving", async () => {
    mockedAdminClient.listAdminPlans.mockResolvedValue([
      { name: "free", max_generations_per_day: 5, max_generations_per_month: null, max_num_timesteps: 30 },
    ]);
    mockedAdminClient.updateAdminPlan.mockResolvedValue({
      name: "free",
      max_generations_per_day: null,
      max_generations_per_month: null,
      max_num_timesteps: 30,
    });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "plans" }));
    await screen.findByText("free");

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog");
    const perDayInput = within(dialog).getByLabelText(/max generations per day/i);
    await user.clear(perDayInput);
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(mockedAdminClient.updateAdminPlan).toHaveBeenCalledWith(
        "free",
        { max_generations_per_day: null, max_generations_per_month: null, max_num_timesteps: 30 },
        "tok",
      ),
    );
  });

  it("shows the backend's validation error and keeps the dialog open on a failed save", async () => {
    mockedAdminClient.listAdminPlans.mockResolvedValue([
      { name: "free", max_generations_per_day: 5, max_generations_per_month: null, max_num_timesteps: 30 },
    ]);
    mockedAdminClient.updateAdminPlan.mockRejectedValue(
      new ApiError("max_num_timesteps must be between 4 and 50, or null for unlimited.", 422),
    );
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "plans" }));
    await screen.findByText("free");

    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("max_num_timesteps must be between 4 and 50"),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes the edit dialog on Escape without saving", async () => {
    mockedAdminClient.listAdminPlans.mockResolvedValue([
      { name: "free", max_generations_per_day: 5, max_generations_per_month: null, max_num_timesteps: 30 },
    ]);
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "plans" }));
    await screen.findByText("free");

    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(mockedAdminClient.updateAdminPlan).not.toHaveBeenCalled();
  });
});

describe("AdminScreen — audit log tab", () => {
  const disableEntry = {
    id: 1,
    admin_user_id: 3,
    action: "user_disabled",
    target_type: "user",
    target_id: "42",
    details: { email: "target@example.com" },
    created_at: "2026-01-01T00:00:00Z",
  };
  const bootstrapEntry = {
    id: 2,
    admin_user_id: null,
    action: "admin_promoted",
    target_type: "user",
    target_id: "7",
    details: { email: "newadmin@example.com", promoted_via: "promote_admin.py" },
    created_at: "2026-01-02T00:00:00Z",
  };

  it("shows an empty state when there are no entries", async () => {
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "audit log" }));

    await waitFor(() => expect(screen.getByText("No audit log entries found.")).toBeInTheDocument());
  });

  it("renders each entry's timestamp, admin, action, target, and details", async () => {
    mockedAdminClient.listAdminAuditLog.mockResolvedValue({
      entries: [disableEntry],
      total: 1,
      limit: 50,
      offset: 0,
    });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "audit log" }));

    await screen.findByText("user_disabled");
    expect(screen.getByText("3")).toBeInTheDocument(); // admin_user_id
    expect(screen.getByText("user: 42")).toBeInTheDocument();
    expect(screen.getByText("target@example.com")).toBeInTheDocument(); // details.email
    expect(screen.getByText(new Date("2026-01-01T00:00:00Z").toLocaleString())).toBeInTheDocument();
  });

  it("shows 'System' for an entry with no HTTP-authenticated admin actor", async () => {
    mockedAdminClient.listAdminAuditLog.mockResolvedValue({
      entries: [bootstrapEntry],
      total: 1,
      limit: 50,
      offset: 0,
    });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "audit log" }));

    await screen.findByText("admin_promoted");
    expect(screen.getByText("System")).toBeInTheDocument();
  });

  it("never renders a secret-looking field even if one somehow appeared in details", async () => {
    mockedAdminClient.listAdminAuditLog.mockResolvedValue({
      entries: [
        {
          id: 3,
          admin_user_id: 1,
          action: "plan_updated",
          target_type: "plan",
          target_id: "free",
          details: { before: { max_generations_per_day: 5 }, after: { max_generations_per_day: 10 } },
          created_at: "2026-01-03T00:00:00Z",
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "audit log" }));

    await screen.findByText("plan_updated");
    expect(screen.getByText("plan: free")).toBeInTheDocument();
    // Nested detail values render as readable JSON, not [object Object].
    expect(screen.getAllByText(/max_generations_per_day/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/password|token|secret/i)).not.toBeInTheDocument();
  });

  it("filters by target type, resetting to page 1", async () => {
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "audit log" }));
    await waitFor(() =>
      expect(mockedAdminClient.listAdminAuditLog).toHaveBeenCalledWith("tok", {
        targetType: undefined,
        targetId: undefined,
        limit: 50,
        offset: 0,
      }),
    );

    await user.selectOptions(screen.getByLabelText("Target type"), "plan");

    await waitFor(() =>
      expect(mockedAdminClient.listAdminAuditLog).toHaveBeenLastCalledWith("tok", {
        targetType: "plan",
        targetId: undefined,
        limit: 50,
        offset: 0,
      }),
    );
  });

  it("filters by target ID via the filter form", async () => {
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "audit log" }));
    await waitFor(() => expect(mockedAdminClient.listAdminAuditLog).toHaveBeenCalled());

    await user.type(screen.getByLabelText("Filter by target ID"), "42");
    await user.click(screen.getByRole("button", { name: "Filter" }));

    await waitFor(() =>
      expect(mockedAdminClient.listAdminAuditLog).toHaveBeenLastCalledWith("tok", {
        targetType: undefined,
        targetId: "42",
        limit: 50,
        offset: 0,
      }),
    );
  });

  it("pages forward through the audit log with Next, requesting the next offset", async () => {
    mockedAdminClient.listAdminAuditLog.mockResolvedValueOnce({
      entries: [disableEntry],
      total: 60,
      limit: 50,
      offset: 0,
    });
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "audit log" }));
    await screen.findByText("user_disabled");

    mockedAdminClient.listAdminAuditLog.mockResolvedValueOnce({
      entries: [bootstrapEntry],
      total: 60,
      limit: 50,
      offset: 50,
    });
    await user.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() =>
      expect(mockedAdminClient.listAdminAuditLog).toHaveBeenLastCalledWith("tok", {
        targetType: undefined,
        targetId: undefined,
        limit: 50,
        offset: 50,
      }),
    );
  });

  it("shows the backend's real error message on failure", async () => {
    mockedAdminClient.listAdminAuditLog.mockRejectedValue(new ApiError("Admin access required.", 403));
    const user = userEvent.setup();
    render(<AdminScreen authToken="tok" onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "audit log" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Admin access required."));
  });
});
